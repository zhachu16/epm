import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from epm import EpmError, Workspace, load_model
from epm.engine.cache import ValueCache
from epm.package import Package, pack, package_bytes, read_package, read_package_bytes, unpack

FIXTURE = Path(__file__).parent / "fixtures" / "three_statement.json"


@pytest.fixture
def ws() -> Workspace:
    return Workspace(load_model(FIXTURE))


def edit(ws: Workspace, name: str, **changes) -> None:
    metrics = dict(ws.model.metrics)
    metrics[name] = metrics[name].model_copy(update=changes)
    ws.replace(ws.model.model_copy(update={"metrics": metrics}))


# -- selective recalculation --------------------------------------------------------------


def test_second_run_reuses_everything(ws):
    first = ws.run()
    assert set(first.computed) == set(ws.model.metrics) and first.reused == []
    second = ws.run()
    assert second.computed == [] and set(second.reused) == set(ws.model.metrics)
    for name, value in first.values.items():
        np.testing.assert_array_equal(np.asarray(value), np.asarray(second.values[name]))
    assert second.checks["balance_sheet_balances"].passed


def test_edit_recomputes_only_downstream(ws):
    ws.run()
    edit(ws, "capex_pct_revenue", value=0.07)
    r = ws.run()
    expected = {"capex_pct_revenue", *ws.engine.trace("capex_pct_revenue", "downstream")}
    assert set(r.computed) == expected
    assert {"revenue", "ebitda", "depreciation", "nwc"} <= set(r.reused)
    assert r.checks["balance_sheet_balances"].passed


def test_overrides_reuse_upstream(ws):
    ws.run()
    r = ws.run(overrides={"tax_rate": 0.30}, outputs=["net_income"])
    assert "revenue" in r.reused and "taxes" in r.computed
    assert "ppe" not in r.computed  # not needed for net_income
    base = ws.run(outputs=["net_income"])
    assert base.computed == []
    assert (r["net_income"] < base["net_income"]).all()


def test_results_are_copies(ws):
    r = ws.run(outputs=["revenue"])
    r["revenue"].iloc[0] = -1
    assert ws.run(outputs=["revenue"])["revenue"].iloc[0] > 0


def test_fingerprint_follows_input_files():
    model = load_model(
        {
            "axes": {"fy": {"values": [1, 2]}},
            "settings": {"default_axis": "fy"},
            "sources": {"s": {"file": "inputs/a.csv", "orientation": "columns"}},
            "metrics": {"x": {"input": {"source": "s", "item": "x"}}, "y": {"formula": "x * 2"}},
        }
    )
    ws = Workspace(model, {"inputs/a.csv": b"t,x\n1,10\n2,20\n"})
    assert list(ws.run()["y"]) == [20, 40]
    ws.set_input("inputs/a.csv", b"t,x\n1,1\n2,2\n")
    r = ws.run()
    assert list(r["y"]) == [2, 4] and set(r.computed) == {"x", "y"}


def test_cache_is_bounded():
    cache = ValueCache(max_entries=3)
    ws = Workspace(load_model(FIXTURE))
    ws.cache = cache
    ws.run()
    assert len(cache) == 3


# -- archives -----------------------------------------------------------------------------


def test_archive_roundtrip_with_cache(ws, tmp_path):
    ws.functions = "# no functions yet\n"
    ws.set_input("inputs/notes.csv", b"a,b\n1,2\n")
    before = ws.run()
    path = ws.save(tmp_path / "toy.epm")
    names = zipfile.ZipFile(path).namelist()
    assert names[:3] == ["model.json", "functions.py", "inputs/notes.csv"]
    assert "cache/index.json" in names and "cache/axis-fy.parquet" in names

    reopened = Workspace.open(path)
    assert reopened.model == ws.model
    assert reopened.functions == ws.functions
    assert reopened.inputs == ws.inputs
    after = reopened.run()
    assert after.computed == []  # everything came from the saved cache
    for name, value in before.values.items():
        np.testing.assert_allclose(np.asarray(value, float), np.asarray(after.values[name], float))
    assert after.status["revenue"].state == "ok"


def test_saved_cache_is_ignored_after_external_edit(ws, tmp_path):
    ws.run()
    path = ws.save(tmp_path / "toy.epm")
    pkg = read_package(path)
    data = json.loads(zipfile.ZipFile(path).read("model.json"))
    data["metrics"]["tax_rate"]["value"] = 0.4
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("model.json", json.dumps(data))
        for name, blob in pkg.cache.items():
            zf.writestr(name, blob)
    r = Workspace.open(path).run()
    assert "taxes" in r.computed and "revenue" in r.reused


def test_saving_is_deterministic_and_atomic(ws, tmp_path):
    a = ws.save(tmp_path / "a.epm", include_cache=False).read_bytes()
    b = ws.save(tmp_path / "a.epm", include_cache=False).read_bytes()
    assert a == b
    assert [p.name for p in tmp_path.iterdir()] == ["a.epm"]  # no temporary files left
    with pytest.raises(EpmError, match="extension"):
        ws.save(tmp_path / "a.zip")


def test_directory_layout_pack_and_unpack(ws, tmp_path):
    ws.set_input("inputs/x.csv", b"1")
    archive = ws.save(tmp_path / "m.epm")
    unpack(archive, tmp_path / "dir")
    assert (tmp_path / "dir" / "model.json").is_file()
    assert (tmp_path / "dir" / "inputs" / "x.csv").read_bytes() == b"1"
    opened = Workspace.open(tmp_path / "dir")
    assert opened.model == ws.model
    opened.remove_input("inputs/x.csv")
    opened.save()  # back into the directory
    assert not (tmp_path / "dir" / "inputs" / "x.csv").exists()
    pack(tmp_path / "dir", tmp_path / "packed.epm")
    assert Workspace.open(tmp_path / "packed.epm").inputs == {}
    with pytest.raises(EpmError, match="not empty"):
        unpack(archive, tmp_path / "dir")


def test_archives_in_memory_match_files(ws, tmp_path):
    ws.set_input("inputs/x.csv", b"1")
    on_disk = ws.save(tmp_path / "m.epm", include_cache=False).read_bytes()
    data = package_bytes(Package(ws.model, ws.inputs, ws.functions), include_cache=False)
    assert data == on_disk
    pkg = read_package_bytes(data)
    assert pkg.model == ws.model and pkg.inputs == {"inputs/x.csv": b"1"}
    with pytest.raises(EpmError, match="not a ZIP"):
        read_package_bytes(b"hello")


def test_bad_archives(tmp_path):
    (tmp_path / "plain.epm").write_text("hello")
    with pytest.raises(EpmError, match="not a ZIP"):
        read_package(tmp_path / "plain.epm")
    with zipfile.ZipFile(tmp_path / "empty.epm", "w") as zf:
        zf.writestr("inputs/a.csv", "x")
    with pytest.raises(EpmError, match="no model.json"):
        read_package(tmp_path / "empty.epm")
    with zipfile.ZipFile(tmp_path / "evil.epm", "w") as zf:
        zf.writestr("model.json", "{}")
        zf.writestr("../escape.txt", "x")
    with pytest.raises(EpmError, match="unsafe path"):
        read_package(tmp_path / "evil.epm")
    with zipfile.ZipFile(tmp_path / "invalid.epm", "w") as zf:
        zf.writestr("model.json", '{"metrics": {"x": {}}}')
    with pytest.raises(EpmError, match="model.json is invalid"):
        read_package(tmp_path / "invalid.epm")


def test_input_paths_are_checked(ws):
    with pytest.raises(EpmError, match="under 'inputs/'"):
        ws.set_input("secrets.csv", b"")
