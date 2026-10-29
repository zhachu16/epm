import json
from pathlib import Path

import openpyxl
import pytest
from typer.testing import CliRunner

from epm import Workspace
from epm.cli import app

runner = CliRunner()


def fm(*args, input=None):
    return runner.invoke(app, [str(a) for a in args], input=input, catch_exceptions=False)


def fmj(*args, input=None):
    result = fm("--json", *args, input=input)
    return json.loads(result.stdout), result.exit_code


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert fm("init", "deal", "--years", "2024-2028", "--historical", "2024").exit_code == 0
    return tmp_path / "deal.epm"


def test_build_and_run_a_model(model):
    assert fm("metric", "set", "growth", "--value", "0.05", "--unit", "%").exit_code == 0
    r = fm(
        "metric",
        "set",
        "revenue",
        "--formula",
        "prev(revenue, init=100) * (1 + growth)",
        "--unit",
        "USD m",
    )
    assert r.exit_code == 0 and "added    metric revenue" in r.stdout
    assert (
        fm(
            "metric",
            "set",
            "margin",
            "--values",
            "2024=0.2,2025=0.21,2026=0.22,2027=0.22,2028=0.23",
        ).exit_code
        == 0
    )
    assert fm("metric", "set", "ebitda", "--formula", "revenue * margin").exit_code == 0

    data, code = fmj("run", "--output", "ebitda")
    assert code == 0 and data["ok"]
    assert data["data"]["values"]["ebitda"]["values"]["2024"] == pytest.approx(105 * 0.2)

    data, _ = fmj("run", "--set", "growth=0.1", "-o", "revenue")
    assert data["data"]["values"]["revenue"]["values"]["2024"] == pytest.approx(110)
    assert Workspace.open(model).model.metrics["growth"].value == 0.05  # base unchanged

    data, _ = fmj("show", "ebitda")
    assert data["data"]["depends_on"] == ["revenue", "margin"] and data["data"]["axis"] == "fy"

    data, _ = fmj("list", "metrics")
    assert [r["name"] for r in data["data"]] == ["growth", "revenue", "margin", "ebitda"]
    data, _ = fmj("list", "inputs")
    assert [r["name"] for r in data["data"]] == ["growth", "margin"]

    data, _ = fmj("trace", "ebitda", "--upstream")
    assert [m["name"] for m in data["data"]["metrics"]] == ["revenue", "margin", "growth"]
    data, _ = fmj("trace", "growth", "--downstream", "--depth", "1")
    assert [m["name"] for m in data["data"]["metrics"]] == ["revenue"]


def test_sweep_rename_delete_and_errors(model):
    fm("metric", "set", "g", "--value", "0.05")
    fm("metric", "set", "rev", "--formula", "prev(rev, init=100) * (1 + g)")
    fm("metric", "set", "exit_rev", "--formula", "last(rev)")
    data, code = fmj("sweep", "--input", "g=0.0,0.1", "--output", "exit_rev,rev@2026")
    assert code == 0
    rows = data["data"]["rows"]
    assert [r["outputs"]["exit_rev"] for r in rows] == pytest.approx([100, 100 * 1.1**5])
    data, code = fmj(
        "sweep", "-i", "g=0:0.04:0.02", "-i", "rev=1", "-o", "exit_rev", "--mode", "zip"
    )
    assert code == 1 and data["errors"][0]["code"] == "invalid_sweep"

    data, code = fmj("metric", "rename", "g", "growth")
    assert (
        code == 0
        and Workspace.open(model).model.metrics["rev"].formula
        == "prev(rev, init=100) * (1 + growth)"
    )
    data, code = fmj("metric", "delete", "growth")
    assert code == 1 and data["errors"][0]["code"] == "in_use"

    data, code = fmj("metric", "set", "bad", "--formula", "rev + revv")
    assert code == 1 and "did you mean rev" in data["errors"][0]["message"]
    assert "bad" not in Workspace.open(model).model.metrics
    data, code = fmj("show", "nothing")
    assert code == 1 and data["errors"][0]["code"] == "not_found"
    data, code = fmj("undo")
    assert code == 1 and data["errors"][0]["code"] == "no_session"


def test_show_settings(model):
    data, code = fmj("show", "settings")
    assert code == 0 and data["data"]["kind"] == "settings"
    assert data["data"]["definition"]["default_axis"] == "fy"
    assert data["data"]["definition"]["key_outputs"] == []  # defaults are shown too
    assert fm("metric", "set", "settings", "--value", "1").exit_code == 0
    data, _ = fmj("show", "settings")
    assert data["data"]["kind"] == "metric"  # an object with the name wins
    data, _ = fmj("show", "settings", "--kind", "settings")
    assert data["data"]["kind"] == "settings"


def test_apply_batches_from_file_and_stdin(model, tmp_path):
    batch = {
        "label": "add assumptions",
        "ops": [
            {"op": "create", "kind": "metric", "name": "a", "def": {"value": 2}},
            {"op": "create", "kind": "metric", "name": "b", "def": {"formula": "a * 3"}},
        ],
    }
    (tmp_path / "changes.json").write_text(json.dumps(batch))
    data, code = fmj("apply", "changes.json", "--dry-run")
    assert code == 0 and not data["data"]["committed"]
    assert data["data"]["impact"]["outputs"]["b"]["after"]["value"] == 6
    assert "a" not in Workspace.open(model).model.metrics
    data, code = fmj("apply", "-", input=json.dumps(batch))
    assert code == 0 and data["data"]["committed"]
    data, _ = fmj(
        "apply",
        "-",
        "--propose",
        input=json.dumps([{"op": "update", "kind": "metric", "name": "a", "set": {"value": 5}}]),
    )
    assert data["warnings"][0]["code"] == "no_session" and not data["data"]["committed"]
    data, code = fmj("validate")
    assert code == 0 and data["data"]["diagnostics"] == []


def test_sources_functions_tables_checks_and_analyses(model, tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "IS"
    ws.append(["USD m", 2024])
    ws.append(["Revenue", 100])
    wb.save(tmp_path / "hist.xlsx")
    assert fm("source", "add", "hist", "--file", "hist.xlsx", "--sheet", "IS").exit_code == 0
    assert fm("metric", "set", "growth", "--value", "0.1", "--unit", "%").exit_code == 0
    r = fm(
        "metric",
        "set",
        "revenue",
        "--history",
        "hist:Revenue",
        "--formula",
        "prev(revenue) * (1 + growth)",
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    (tmp_path / "f.py").write_text(
        "from epm import function\nimport numpy as np\n\n\n"
        "@function\ndef bump(x, by=1.0):\n    return np.asarray(x) + by\n"
    )
    assert fm("function", "set", "bump", "--file", "f.py").exit_code == 0
    assert fm("metric", "set", "bumped", "--formula", "bump(revenue)").exit_code == 0
    assert fm("table", "set", "summary", "--rows", "#Top line,revenue,bumped:total").exit_code == 0
    assert fm("check", "set", "positive", "--expr", "revenue > 0").exit_code == 0
    assert (
        fm(
            "analysis",
            "set",
            "growth_line",
            "-i",
            "growth=0.0,0.05,0.1",
            "-o",
            "revenue@2028",
            "--plot",
            "line",
        ).exit_code
        == 0
    )

    data, code = fmj("table", "show", "summary")
    assert code == 0
    assert data["data"]["rows"][1]["value"]["values"]["2025"] == pytest.approx(110)
    assert data["data"]["rows"][2]["value"]["values"]["2024"] == pytest.approx(101)

    data, code = fmj("run")
    assert code == 0 and data["data"]["checks"]["positive"]["state"] == "passed"

    pytest.importorskip("matplotlib")
    data, code = fmj("analysis", "run", "growth_line", "--plot", str(tmp_path / "plot.png"))
    assert code == 0 and (tmp_path / "plot.png").exists()
    assert data["data"]["chart"]["x_values"] == [0.0, 0.05, 0.1]

    data, _ = fmj("list", "functions")
    assert data["data"][0]["signature"] == "bump(x, by)"
    data, _ = fmj("list", "files")
    assert data["data"][0]["name"] == "inputs/hist.xlsx"
    Workspace.open(model).close()


def test_model_discovery_and_packaging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data, code = fmj("list")
    assert code == 1 and data["errors"][0]["code"] == "no_model"
    fm("init", "a.epm")
    fm("init", "b.epm")
    data, code = fmj("list")
    assert data["errors"][0]["code"] == "ambiguous_model"
    assert fm("-m", "a.epm", "metric", "set", "x", "--value", "1").exit_code == 0
    assert fm("unpack", "a.epm", "a_dir").exit_code == 0
    assert (Path("a_dir") / "model.json").is_file()
    assert fm("-m", "a_dir", "metric", "set", "y", "--formula", "x + 1").exit_code == 0
    assert fm("pack", "a_dir", "c.epm").exit_code == 0
    data, _ = fmj("-m", "c.epm", "run", "-o", "y")
    assert data["data"]["values"]["y"]["value"] == 2
    data, code = fmj("init", "a.epm")
    assert code == 1 and data["errors"][0]["code"] == "exists"
