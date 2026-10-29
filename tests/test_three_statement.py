"""End-to-end test on a small integrated three-statement model with a cash sweep.

The expected numbers come from a plain Python loop written independently of the engine.
"""

from pathlib import Path

import numpy as np
import pytest

from fm import Engine, load_model

FIXTURE = Path(__file__).parent / "fixtures" / "three_statement.json"
YEARS = [2025, 2026, 2027, 2028, 2029, 2030]


def reference_model() -> dict[str, list[float]]:
    growth, margin, da, capex_pct, nwc_pct = 0.06, 0.22, 0.04, 0.05, 0.10
    tax, rate, amort, min_cash = 0.25, 0.07, 25.0, 30.0
    revenue, cash, ppe, nwc, debt = 1000.0, 50.0, 400.0, 100.0, 350.0
    equity = cash + ppe + nwc - debt
    out: dict[str, list[float]] = {
        k: [] for k in ("revenue", "net_income", "sweep", "debt", "cash", "equity", "fcf")
    }
    for _ in YEARS:
        revenue *= 1 + growth
        ebitda = revenue * margin
        dep = revenue * da
        interest = rate * debt
        ebt = ebitda - dep - interest
        net_income = ebt - max(ebt, 0.0) * tax
        new_nwc = revenue * nwc_pct
        fcf = net_income + dep - (new_nwc - nwc) - revenue * capex_pct
        mandatory = min(amort, debt)
        available = cash + fcf - mandatory - min_cash
        sweep = max(min(available, debt - mandatory), 0.0)
        debt = debt - mandatory - sweep
        cash = cash + fcf - mandatory - sweep
        ppe = ppe + revenue * capex_pct - dep
        nwc = new_nwc
        equity += net_income
        for k, v in (
            ("revenue", revenue),
            ("net_income", net_income),
            ("sweep", sweep),
            ("debt", debt),
            ("cash", cash),
            ("equity", equity),
            ("fcf", fcf),
        ):
            out[k].append(v)
    return out


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine(load_model(FIXTURE))


def test_model_is_valid(engine):
    assert engine.diagnostics == []


def test_matches_reference(engine):
    result = engine.run()
    assert result.ok, [d.to_dict() for d in result.diagnostics]
    ref = reference_model()
    for name in ("revenue", "net_income", "sweep", "debt", "cash", "equity"):
        np.testing.assert_allclose(result[name].to_numpy(), ref[name], rtol=1e-12, err_msg=name)
    assert list(result["revenue"].index) == YEARS
    assert result["cumulative_fcf"] == pytest.approx(sum(ref["fcf"]))
    assert result["exit_debt"] == pytest.approx(ref["debt"][-1])


def test_checks_pass(engine):
    result = engine.run()
    assert result.checks["balance_sheet_balances"].passed
    assert result.checks["cash_above_minimum"].passed


def test_shapes_are_inferred(engine):
    assert engine.shape("ebitda") == "fy"
    assert engine.shape("opening_equity") is None
    assert engine.shape("cumulative_fcf") is None


def test_debt_and_cash_form_one_recurrence(engine):
    recurrences = [s.members for s in engine.steps if s.kind == "recurrence"]
    debt_group = next(g for g in recurrences if "debt" in g)
    assert {"cash", "sweep", "interest", "net_income", "opening_debt_balance"} <= set(debt_group)
    # equity reads net_income but nothing in the debt group reads equity
    assert "equity" not in debt_group
    assert ["equity"] in recurrences
    # same-period order inside the group: the sweep needs the cash available, debt needs sweep
    order = debt_group.index
    assert order("cash_available_for_sweep") < order("sweep") < order("debt")


def test_override_does_not_change_base(engine):
    base = engine.run(outputs=["cumulative_fcf"])["cumulative_fcf"]
    pricier = engine.run(overrides={"interest_rate": 0.12}, outputs=["cumulative_fcf"])
    assert pricier["cumulative_fcf"] < base
    assert engine.run(outputs=["cumulative_fcf"])["cumulative_fcf"] == base
    assert engine.model.metrics["interest_rate"].value == 0.07


def test_outputs_limit_evaluation(engine):
    result = engine.run(outputs=["ebitda"])
    assert set(result.values) == {"ebitda"}
    assert set(result.status) == {"ebitda"}
    assert result.checks == {}


def test_trace(engine):
    assert set(engine.trace("ebit", depth=1)) == {"ebitda", "depreciation"}
    up = engine.trace("ebit")
    assert "base_revenue" in up and "revenue_growth" in up
    assert "net_income" in engine.trace("ebit", direction="downstream", depth=2)


def test_breaking_the_balance_sheet_fails_the_check():
    model = load_model(FIXTURE)
    model.metrics["ppe"].formula = "prev(ppe, init=opening_ppe) + capex"  # forgot depreciation
    result = Engine(model).run()
    check = result.checks["balance_sheet_balances"]
    assert not check.passed
    assert [f["period"] for f in check.failing] == [str(y) for y in YEARS]
    assert not result.ok
