from pathlib import Path

import pytest

from epm import EpmError, Workspace, load_model

FIXTURE = Path(__file__).parent / "fixtures" / "three_statement.json"


@pytest.fixture
def ws() -> Workspace:
    return Workspace(load_model(FIXTURE))


def test_grid_sweep_preserves_base(ws):
    base = ws.run(outputs=["cumulative_fcf"])["cumulative_fcf"]
    result = ws.sweep(
        {"interest_rate": [0.05, 0.07, 0.09], "tax_rate": [0.2, 0.3]},
        ["cumulative_fcf", "debt@2027"],
    )
    assert result.ok and len(result.rows) == 6
    frame = result.frame()
    assert list(frame.columns) == [
        "interest_rate",
        "tax_rate",
        "cumulative_fcf",
        "debt@2027",
        "status",
        "error",
    ]
    row = frame[(frame.interest_rate == 0.07) & (frame.tax_rate == 0.3)].iloc[0]
    other = frame[(frame.interest_rate == 0.07) & (frame.tax_rate == 0.2)].iloc[0]
    assert row.cumulative_fcf < other.cumulative_fcf
    assert ws.model.metrics["interest_rate"].value == 0.07
    assert ws.run(outputs=["cumulative_fcf"])["cumulative_fcf"] == base


def test_zip_sweep_and_errors(ws):
    r = ws.sweep({"interest_rate": [0.05, 0.06], "tax_rate": [0.2, 0.3]}, ["exit_debt"], mode="zip")
    assert [row["inputs"] for row in r.rows] == [
        {"interest_rate": 0.05, "tax_rate": 0.2},
        {"interest_rate": 0.06, "tax_rate": 0.3},
    ]
    with pytest.raises(EpmError, match="same number of values"):
        ws.sweep({"interest_rate": [0.05], "tax_rate": [0.2, 0.3]}, ["exit_debt"], mode="zip")
    with pytest.raises(EpmError, match="pick a period"):
        ws.sweep({"interest_rate": [0.05]}, ["debt"])
    with pytest.raises(EpmError, match="not a period"):
        ws.sweep({"interest_rate": [0.05]}, ["debt@1999"])
    with pytest.raises(EpmError, match="unknown metric 'intrest_rate'"):
        ws.sweep({"intrest_rate": [0.05]}, ["exit_debt"])


def test_failing_scenarios_are_recorded():
    ws = Workspace(
        load_model(
            {
                "metrics": {
                    "a": {"value": 0.5},
                    "x": {"formula": "a * y + 1"},
                    "y": {"formula": "x"},
                },
                "circularities": {"loop": {"metrics": ["x", "y"], "max_iter": 200}},
            }
        )
    )
    r = ws.sweep({"a": [0.5, 2.0]}, ["x"])
    assert r.rows[0]["status"] == "ok" and r.rows[0]["outputs"]["x"] == pytest.approx(2.0)
    assert r.rows[1]["status"] == "error" and "circularity 'loop'" in r.rows[1]["error"]
    assert not r.ok


ANALYSES = [
    {
        "op": "create",
        "kind": "analysis",
        "name": "rate_line",
        "def": {
            "inputs": [
                {"metric": "interest_rate", "range": {"start": 0.04, "stop": 0.1, "step": 0.02}}
            ],
            "outputs": ["cumulative_fcf", "exit_debt"],
            "plot": {"kind": "line", "x": "interest_rate", "y": ["cumulative_fcf"]},
        },
    },
    {
        "op": "create",
        "kind": "analysis",
        "name": "rate_tax_grid",
        "def": {
            "inputs": [
                {"metric": "interest_rate", "values": [0.05, 0.07]},
                {"metric": "tax_rate", "values": [0.2, 0.25, 0.3]},
            ],
            "outputs": ["cumulative_fcf"],
            "overrides": {"min_cash": 40},
            "plot": {
                "kind": "heatmap",
                "x": "interest_rate",
                "y": "tax_rate",
                "z": "cumulative_fcf",
            },
        },
    },
]


def test_saved_analyses(ws, tmp_path):
    assert ws.apply(ANALYSES).ok
    line = ws.run_analysis("rate_line")
    chart = line.chart()
    assert chart["x_values"] == [0.04, 0.06, 0.08, 0.1]
    assert list(chart["series"]) == ["cumulative_fcf"]
    grid = ws.run_analysis("rate_tax_grid").chart()
    assert grid["x_values"] == [0.05, 0.07] and grid["y_values"] == [0.2, 0.25, 0.3]
    assert len(grid["values"]) == 3 and len(grid["values"][0]) == 2

    # analyses follow renames and block deletes
    assert ws.apply(
        [{"op": "rename", "kind": "metric", "name": "exit_debt", "to": "final_debt"}]
    ).ok
    assert ws.model.analyses["rate_line"].outputs == ["cumulative_fcf", "final_debt"]
    assert ws.apply([{"op": "rename", "kind": "metric", "name": "tax_rate", "to": "tax"}]).ok
    assert ws.model.analyses["rate_tax_grid"].plot.y == "tax"
    r = ws.apply([{"op": "delete", "kind": "metric", "name": "min_cash"}])
    assert not r.ok and "analysis 'rate_tax_grid'" in r.errors[0].message
    # analyses rerun against the edited model
    assert ws.apply([{"op": "update", "kind": "metric", "name": "tax", "set": {"value": 0.4}}]).ok
    assert ws.run_analysis("rate_tax_grid").sweep.ok

    pytest.importorskip("matplotlib")
    ws.run_analysis("rate_line").save_plot(str(tmp_path / "line.png"))
    ws.run_analysis("rate_tax_grid").save_plot(str(tmp_path / "grid.png"))
    assert (tmp_path / "line.png").stat().st_size > 1000


def test_analysis_validation(ws):
    bad = dict(ANALYSES[0], name="bad")
    bad["def"] = {**bad["def"], "outputs": ["debt"], "plot": None}
    r = ws.apply([bad])
    assert not r.ok and "pick a period" in r.errors[0].message
    with pytest.raises(ValueError, match="heatmap needs exactly two inputs"):
        load_model(
            {
                "analyses": {
                    "h": {
                        "inputs": [{"metric": "a", "values": [1]}],
                        "outputs": ["b"],
                        "plot": {"kind": "heatmap", "x": "a", "y": "a", "z": "b"},
                    }
                }
            }
        )


def test_set_values_only_changes_fixed_values(ws):
    engine = ws.engine.with_overrides({"interest_rate": 0.05})
    engine.set_values({"interest_rate": 0.09})
    assert engine.run(outputs=["interest_rate"])["interest_rate"] == 0.09
    with pytest.raises(ValueError, match="single fixed value"):
        engine.set_values({"cumulative_fcf": 1.0})
