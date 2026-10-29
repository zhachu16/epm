import base64
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from epm import EpmError, Workspace, dump_model, load_model
from epm.ops import Op
from epm.ops.rewrite import rename_axis_literal, rename_function, rename_metric

FIXTURE = Path(__file__).parent / "fixtures" / "three_statement.json"


@pytest.fixture
def ws() -> Workspace:
    return Workspace(load_model(FIXTURE))


def op(**kw) -> dict:
    return kw


# -- formula rewriting --------------------------------------------------------------------


def test_rename_metric_text_is_surgical():
    text = "ebit  +  ebitda*(1 + ebit)"
    assert rename_metric(text, "ebit", "op_income") == "op_income  +  ebitda*(1 + op_income)"
    assert rename_metric("max(ebit, 0)", "max", "x") == "max(ebit, 0)"  # calls are untouched
    assert rename_metric("a +", "a", "b") == "a +"  # invalid formulas are left alone
    assert (
        rename_metric("prev(\n  cash,\n  init=cash0)", "cash", "c") == "prev(\n  c,\n  init=cash0)"
    )
    assert rename_function("amort(x) + amort", "amort", "f") == "f(x) + amort"
    assert rename_axis_literal("is_forecast('fy') + fy", "fy", "year") == "is_forecast('year') + fy"
    assert rename_metric("'é' if é2 else ebit", "ebit", "e") == "'é' if é2 else e"


# -- basic edits and history --------------------------------------------------------------


def test_create_update_delete_and_undo(ws):
    before = dump_model(ws.model)
    r = ws.apply(
        [
            op(op="create", kind="metric", name="fees", **{"def": {"value": 12, "unit": "USD m"}}),
            op(
                op="create",
                kind="metric",
                name="ebit_after_fees",
                **{"def": {"formula": "ebit - fees"}},
            ),
        ]
    )
    assert r.ok and r.committed, r.to_dict()
    assert {c.name for c in r.changes} == {"fees", "ebit_after_fees"}
    assert ws.run(outputs=["ebit_after_fees"]).status["ebit_after_fees"].state == "ok"

    r = ws.apply(
        [
            op(
                op="update",
                kind="metric",
                name="fees",
                set={"value": None, "formula": "0.01 * revenue"},
            )
        ]
    )
    assert r.ok
    assert (
        ws.model.metrics["fees"].formula == "0.01 * revenue"
        and ws.model.metrics["fees"].value is None
    )

    assert len(ws.history) == 2
    ws.undo()
    assert ws.model.metrics["fees"].value == 12
    ws.undo()
    assert dump_model(ws.model) == before
    with pytest.raises(EpmError, match="nothing to undo"):
        ws.undo()
    ws.redo()
    ws.redo()
    assert ws.model.metrics["fees"].formula == "0.01 * revenue"


def test_upsert_and_settings(ws):
    assert ws.apply([op(op="upsert", kind="metric", name="tax_rate", set={"value": 0.3})]).ok
    assert ws.model.metrics["tax_rate"].value == 0.3
    assert ws.apply([op(op="upsert", kind="metric", name="new_one", set={"value": 1})]).ok
    assert "new_one" in ws.model.metrics
    r = ws.apply(
        [op(op="update", kind="settings", set={"name": "Renamed", "key_outputs": ["exit_debt"]})]
    )
    assert r.ok and ws.model.settings.name == "Renamed"
    ws.undo()
    assert ws.model.settings.name == "Toy three-statement model"
    assert ws.model.settings.key_outputs == []


def test_batches_are_atomic_and_report_every_error(ws):
    before = dump_model(ws.model)
    r = ws.apply(
        [
            op(op="update", kind="metric", name="tax_rate", set={"value": 0.3}),
            op(op="create", kind="metric", name="ebit", **{"def": {"value": 1}}),
            op(op="delete", kind="metric", name="nope"),
            op(op="create", kind="metric", name="bad", **{"def": {"value": 1, "formula": "2"}}),
            op(op="create", kind="metric", name="total", **{"def": {"value": 1}}),
        ]
    )
    assert not r.ok and not r.committed
    assert [(e.index, e.code) for e in r.errors] == [
        (1, "already_exists"),
        (2, "not_found"),
        (3, "invalid_definition"),
        (4, "invalid_name"),
    ]
    assert dump_model(ws.model) == before and ws.history == []


def test_new_validation_errors_reject_the_batch(ws):
    r = ws.apply(
        [op(op="update", kind="metric", name="ebit", set={"formula": "ebitda - deprecation"})]
    )
    assert not r.ok
    assert (
        r.errors[0].code == "unknown_reference"
        and "did you mean depreciation" in r.errors[0].message
    )
    assert ws.model.metrics["ebit"].formula == "ebitda - depreciation"
    r = ws.apply(
        [op(op="update", kind="metric", name="ebit", set={"formula": "ebitda - deprecation"})],
        allow_errors=True,
    )
    assert r.ok and r.new_errors


def test_forward_references_within_a_batch_are_fine(ws):
    r = ws.apply(
        [
            op(op="create", kind="metric", name="a", **{"def": {"formula": "b * 2"}}),
            op(op="create", kind="metric", name="b", **{"def": {"value": 3}}),
        ]
    )
    assert r.ok and ws.run(outputs=["a"])["a"] == 6


# -- rename and delete -------------------------------------------------------------------


def test_rename_metric_updates_every_reference(ws):
    ws.apply(
        [
            op(op="update", kind="settings", set={"key_outputs": ["net_income"]}),
            op(
                op="create",
                kind="circularity",
                name="c",
                **{"def": {"metrics": ["net_income", "taxes"]}},
            ),
        ],
        allow_errors=True,
    )
    r = ws.apply(
        [op(op="rename", kind="metric", name="net_income", to="profit")], allow_errors=True
    )
    assert r.ok, r.to_dict()
    m = ws.model
    assert "profit" in m.metrics and "net_income" not in m.metrics
    assert list(m.metrics).index("profit") == list(load_model(FIXTURE).metrics).index("net_income")
    assert m.metrics["cfo"].formula == "profit + depreciation - change_in_nwc"
    assert m.metrics["equity"].formula == "prev(equity, init=opening_equity) + profit"
    assert m.tables["income_statement"].rows[-1].metric == "profit"
    assert m.circularities["c"].metrics == ["profit", "taxes"]
    assert m.settings.key_outputs == ["profit"]
    [change] = [c for c in r.changes if c.change == "renamed"]
    assert (change.name, change.renamed_from) == ("profit", "net_income")
    assert {c.name for c in r.changes if c.change == "changed"} >= {"cfo", "equity"}
    ws.undo()
    assert ws.model.metrics["cfo"].formula == "net_income + depreciation - change_in_nwc"


def test_delete_in_use_fails_with_users(ws):
    r = ws.apply([op(op="delete", kind="metric", name="depreciation")])
    assert not r.ok
    used_by = r.errors[0].detail["used_by"]
    assert "metric 'ebit'" in used_by and "table 'income_statement'" in used_by
    assert ws.apply([op(op="delete", kind="metric", name="exit_debt")]).ok


def test_rename_axis_and_source_and_files():
    model = load_model(
        {
            "settings": {"default_axis": "fy"},
            "axes": {"fy": {"values": [2024, 2025], "historical": [2024]}},
            "sources": {"ops": {"file": "inputs/ops.csv", "orientation": "columns", "axis": "fy"}},
            "metrics": {
                "volume": {"input": {"source": "ops", "item": "volume"}},
                "flag": {"formula": "where(is_forecast('fy'), volume, 0)"},
            },
        }
    )
    ws = Workspace(model, {"inputs/ops.csv": b"year,volume\n2024,5\n2025,7\n"})
    assert ws.run().ok
    assert ws.apply([op(op="rename", kind="axis", name="fy", to="year")]).ok
    assert ws.model.settings.default_axis == "year"
    assert ws.model.sources["ops"].axis == "year"
    assert ws.model.metrics["flag"].formula == "where(is_forecast('year'), volume, 0)"
    assert ws.apply([op(op="rename", kind="source", name="ops", to="operations")]).ok
    assert ws.model.metrics["volume"].input.source == "operations"

    r = ws.apply([op(op="rename", kind="file", name="inputs/ops.csv", to="inputs/operations.csv")])
    assert r.ok and ws.model.sources["operations"].file == "inputs/operations.csv"
    assert [c.change for c in r.changes if c.kind == "file"] == ["renamed"]
    assert not ws.apply([op(op="delete", kind="file", name="inputs/operations.csv")]).ok
    new_csv = base64.b64encode(b"year,volume\n2024,1\n2025,2\n").decode()
    r = ws.apply([op(op="update", kind="file", name="inputs/operations.csv", data=new_csv)])
    assert r.ok
    assert list(ws.run()["flag"]) == [0, 2]
    assert r.impact.outputs == {}  # no metric definitions changed
    ws.undo()
    assert list(ws.run()["flag"]) == [0, 7]
    assert not ws.apply([op(op="create", kind="file", name="other/x.csv", data=new_csv)]).ok


# -- previews and pending batches -----------------------------------------------------------


def test_dry_run_shows_impact_without_changing_anything(ws):
    before = dump_model(ws.model)
    r = ws.apply(
        [op(op="update", kind="metric", name="tax_rate", set={"value": 0.35})],
        mode="dry_run",
        outputs=["cumulative_fcf", "net_income"],
    )
    assert r.ok and not r.committed and dump_model(ws.model) == before
    fcf = r.impact.outputs["cumulative_fcf"]
    assert fcf["after"]["value"] < fcf["before"]["value"]
    assert fcf["delta"] == pytest.approx(fcf["after"]["value"] - fcf["before"]["value"])
    assert set(r.impact.outputs["net_income"]["delta"]) == {
        "2025",
        "2026",
        "2027",
        "2028",
        "2029",
        "2030",
    }
    change = r.changes[0].to_dict()
    assert change["before"]["value"] == 0.25 and change["after"]["value"] == 0.35
    assert r.to_dict()["impact"]["outputs"]["cumulative_fcf"]["status"] == "ok"


def test_preview_reports_broken_checks(ws):
    r = ws.apply(
        [
            op(
                op="update",
                kind="metric",
                name="ppe",
                set={"formula": "prev(ppe, init=opening_ppe) + capex"},
            )
        ],
        mode="dry_run",
    )
    assert r.impact.checks["balance_sheet_balances"] == {"before": "passed", "after": "failed"}


def test_propose_commit_and_reject(ws):
    r = ws.apply(
        [op(op="update", kind="metric", name="tax_rate", set={"value": 0.3})], mode="propose"
    )
    assert r.ok and r.id in ws.pending and ws.model.metrics["tax_rate"].value == 0.25
    other = ws.apply(
        [op(op="update", kind="metric", name="min_cash", set={"value": 10})], mode="propose"
    )
    committed = ws.commit_pending(r.id)
    assert committed.committed and ws.model.metrics["tax_rate"].value == 0.3
    assert list(ws.pending) == [other.id]
    ws.reject_pending(other.id)
    assert ws.pending == {} and ws.model.metrics["min_cash"].value == 30
    with pytest.raises(EpmError):
        ws.commit_pending("missing")


def test_rollback(ws):
    first = ws.apply([op(op="update", kind="metric", name="tax_rate", set={"value": 0.3})])
    ws.apply([op(op="update", kind="metric", name="tax_rate", set={"value": 0.31})])
    ws.apply([op(op="update", kind="metric", name="min_cash", set={"value": 1})])
    undone = ws.rollback(first.id)
    assert len(undone) == 2 and ws.model.metrics["tax_rate"].value == 0.3
    assert ws.model.metrics["min_cash"].value == 30
    with pytest.raises(EpmError):
        ws.rollback("nope")


def test_edits_recompute_selectively(ws):
    ws.run()
    r = ws.apply([op(op="update", kind="metric", name="capex_pct_revenue", set={"value": 0.06})])
    assert r.ok
    run = ws.run()
    # the preview already evaluated what changed, so the run only adds what it skipped
    downstream = {"capex_pct_revenue", *ws.trace("capex_pct_revenue", "downstream")}
    assert set(run.computed) <= downstream
    assert "revenue" in run.reused and "capex" in run.reused


def test_op_schema():
    with pytest.raises(ValidationError, match="needs 'def'"):
        Op(op="create", kind="metric", name="x")
    with pytest.raises(ValidationError, match="needs 'to'"):
        Op(op="rename", kind="metric", name="x")
    with pytest.raises(ValidationError, match="settings only support"):
        Op(op="delete", kind="settings")
    assert Op.model_validate(
        {"op": "create", "kind": "metric", "name": "x", "def": {"value": 1}}
    ).to_dict() == {
        "op": "create",
        "kind": "metric",
        "name": "x",
        "def": {"value": 1},
    }


def test_random_edit_sequences_undo_to_the_start(ws):
    rng = random.Random(7)
    start = dump_model(ws.model)
    names = list(ws.model.metrics)
    for i in range(40):
        name = rng.choice(names)
        choice = rng.random()
        if choice < 0.4:
            ops = [op(op="update", kind="metric", name=name, set={"description": f"note {i}"})]
        elif choice < 0.7:
            new = f"{name}_{i}"
            ops = [op(op="rename", kind="metric", name=name, to=new)]
        else:
            ops = [
                op(
                    op="create",
                    kind="metric",
                    name=f"extra_{i}",
                    **{"def": {"formula": f"{name} * 2"}},
                )
            ]
        r = ws.apply(ops, allow_errors=True)
        if r.ok and ops[0]["op"] == "rename":
            names[names.index(name)] = ops[0]["to"]
    assert len(ws.history) > 30
    while ws.history:
        ws.undo()
    assert dump_model(ws.model) == start
