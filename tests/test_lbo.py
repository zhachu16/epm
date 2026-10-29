"""Golden tests for the sample LBO in examples/lbo.

``reference()`` is an independent plain-Python implementation of the same deal, written
like a spreadsheet: one loop over forecast years, with each year's average-balance
interest solved by its own fixed-point iteration on the closing balances. The engine must
match it to tight tolerances.
"""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from epm import Workspace
from epm.cli import app

LBO = Path(__file__).parents[1] / "examples" / "lbo"
FORECAST = [2025, 2026, 2027, 2028, 2029, 2030]


def reference() -> dict:
    growth = [0.08, 0.07, 0.06, 0.05, 0.05, 0.04]
    gross_margin, sga_pct, da_pct, capex_pct, tax = 0.42, 0.19, 0.04, 0.045, 0.25
    dso, dio, dpo = 50, 60, 40
    entry, exit_multiple, txn_pct, fin_pct, min_cash = 9.0, 9.0, 0.02, 0.025, 30.0
    tlb_rate, rev_rate, notes_rate = 0.04 + 0.035, 0.04 + 0.03, 0.085
    amort_pct, sweep_pct = 0.01, 0.75

    ltm = 1000.0 - 580.0 - 190.0
    ev = entry * ltm
    tlb_amount, notes_amount = 4.0 * ltm, 1.5 * ltm
    fees = txn_pct * ev + fin_pct * (tlb_amount + notes_amount)
    uses = ev + fees + min_cash
    sponsor = uses - tlb_amount - notes_amount
    goodwill = ev - (137.0 + 95.0 - 64.0 + 345.0)
    equity = sponsor - fees

    revenue, nwc, ppe, cash = 1000.0, 137.0 + 95.0 - 64.0, 345.0, min_cash
    tlb, notes, revolver = tlb_amount, notes_amount, 0.0
    out: dict = {
        k: []
        for k in ("revenue", "ebitda", "net_income", "interest", "tlb", "cash", "equity", "check")
    }
    for g in growth:
        revenue *= 1 + g
        cogs = revenue * (1 - gross_margin)
        ebitda = revenue - cogs - revenue * sga_pct
        da = revenue * da_pct
        ar, inv, ap = revenue * dso / 365, cogs * dio / 365, cogs * dpo / 365
        new_nwc = ar + inv - ap
        capex = revenue * capex_pct
        mandatory = min(tlb_amount * amort_pct, tlb)

        tlb_close, rev_close = tlb, revolver  # guess: no change
        for _ in range(500):
            interest = (
                tlb_rate * (tlb + tlb_close) / 2
                + notes_rate * notes
                + rev_rate * (revolver + rev_close) / 2
            )
            ebt = ebitda - da - interest
            net_income = ebt - max(ebt, 0) * tax
            fcf = net_income + da - (new_nwc - nwc) - capex
            available = cash + fcf - mandatory - min_cash
            draw = max(-available, 0.0)
            repay = min(max(available, 0.0), revolver)
            sweep = max(min(sweep_pct * (max(available, 0.0) - repay), tlb - mandatory), 0.0)
            new_tlb, new_rev = tlb - mandatory - sweep, revolver + draw - repay
            done = abs(new_tlb - tlb_close) < 1e-13 and abs(new_rev - rev_close) < 1e-13
            tlb_close, rev_close = new_tlb, new_rev
            if done:
                break
        cash = cash + fcf - mandatory - sweep + draw - repay
        equity += net_income
        nwc, ppe = new_nwc, ppe + capex - da
        tlb, revolver = tlb_close, rev_close
        assets = cash + ar + inv + ppe + goodwill
        liabilities = ap + tlb + notes + revolver + equity
        for k, v in (
            ("revenue", revenue),
            ("ebitda", ebitda),
            ("net_income", net_income),
            ("interest", interest),
            ("tlb", tlb),
            ("cash", cash),
            ("equity", equity),
            ("check", assets - liabilities),
        ):
            out[k].append(v)

    def exit_value(year):
        i = FORECAST.index(year)
        debt = out["tlb"][i] + notes  # the revolver is undrawn in this case
        return exit_multiple * out["ebitda"][i] - (debt - out["cash"][i])

    out["sponsor_equity"] = sponsor
    out["total_uses"] = uses
    out["moic"] = exit_value(2029) / sponsor
    out["equity_irr"] = out["moic"] ** (1 / 5) - 1
    out["moic_3y"] = exit_value(2027) / sponsor
    out["equity_irr_3y"] = out["moic_3y"] ** (1 / 3) - 1
    return out


@pytest.fixture(scope="module")
def workspace():
    ws = Workspace.open(LBO)
    yield ws
    ws.close()


@pytest.fixture(scope="module")
def result(workspace):
    return workspace.run()


def forecast(series):
    return series.loc[FORECAST].to_numpy(dtype=float)


def test_model_is_clean(workspace, result):
    assert workspace.validate() == []
    assert result.ok and result.diagnostics == []
    assert all(c.passed for c in result.checks.values())
    stats = result.solver["average_balance_interest"]
    assert stats.converged and stats.iterations < 20


def test_matches_reference(result):
    ref = reference()
    np.testing.assert_allclose(forecast(result["revenue"]), ref["revenue"], rtol=1e-12)
    np.testing.assert_allclose(forecast(result["ebitda"]), ref["ebitda"], rtol=1e-12)
    np.testing.assert_allclose(forecast(result["interest_expense"]), ref["interest"], rtol=1e-8)
    np.testing.assert_allclose(forecast(result["net_income"]), ref["net_income"], rtol=1e-8)
    np.testing.assert_allclose(forecast(result["tlb_close"]), ref["tlb"], rtol=1e-8)
    np.testing.assert_allclose(forecast(result["cash"]), ref["cash"], rtol=1e-8)
    np.testing.assert_allclose(forecast(result["equity"]), ref["equity"], rtol=1e-8)
    np.testing.assert_allclose(ref["check"], 0, atol=1e-8)  # the reference balances too
    assert result["sponsor_equity"] == pytest.approx(ref["sponsor_equity"], rel=1e-12)
    assert result["total_uses"] == pytest.approx(ref["total_uses"], rel=1e-12)
    for name in ("moic", "equity_irr", "moic_3y", "equity_irr_3y"):
        assert result[name] == pytest.approx(ref[name], rel=1e-8), name


def test_headline_numbers(result):
    """Regression guard on the published example's headline results."""
    assert result["enterprise_value"] == pytest.approx(2070.0)
    assert result["sponsor_equity"] == pytest.approx(908.025)
    assert result["equity_irr"] == pytest.approx(0.1735, abs=5e-5)
    assert result["moic"] == pytest.approx(2.225, abs=5e-4)
    assert result["equity_irr_3y"] == pytest.approx(0.1904, abs=5e-5)


def test_history_and_scope(result):
    revenue = result["revenue"]
    assert list(revenue.loc[[2022, 2023, 2024]]) == [820, 900, 1000]
    assert result["tlb_close"].loc[[2022, 2023, 2024]].isna().all()  # forecast-only
    assert result["interest_expense"].loc[2024] == 18  # pre-deal actual
    assert result["tlb_amortization"].loc[2025] == pytest.approx(9.2)  # custom function


def test_saved_analyses(workspace):
    grid = workspace.run_analysis("entry_exit_irr")
    assert grid.sweep.ok and len(grid.sweep.rows) == 25
    values = np.array(grid.chart()["values"], dtype=float)  # rows: exit multiple, cols: entry
    assert (np.diff(values, axis=1) < 0).all() and (np.diff(values, axis=0) > 0).all()
    line = workspace.run_analysis("interest_sensitivity").chart()
    irrs = line["series"]["equity_irr"]
    assert all(a > b for a, b in zip(irrs, irrs[1:], strict=False))
    lev = workspace.run_analysis("leverage_sensitivity").sweep
    assert lev.ok and [r["outputs"]["leverage@2025"] for r in lev.rows] == sorted(
        r["outputs"]["leverage@2025"] for r in lev.rows
    )


def test_edit_preview_and_selective_recalc(tmp_path):
    shutil.copytree(LBO, tmp_path / "lbo", ignore=shutil.ignore_patterns("cache"))
    ws = Workspace.open(tmp_path / "lbo")
    try:
        ws.run()
        r = ws.apply(
            [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.30}}]
        )
        assert r.ok
        assert r.impact.outputs["equity_irr"]["delta"] < 0
        run = ws.run()
        assert "revenue" in run.reused and "ltm_ebitda" in run.reused
        downstream = {"tax_rate", *ws.trace("tax_rate", "downstream")}
        assert set(run.computed) <= downstream  # the preview already did the rest
        path = ws.save(tmp_path / "atlas.epm")
    finally:
        ws.close()
    reopened = Workspace.open(path)
    try:
        assert reopened.run().computed == []  # every value came from the saved cache
    finally:
        reopened.close()


def test_spec_cli_examples(tmp_path, monkeypatch):
    shutil.copytree(LBO, tmp_path / "lbo", ignore=shutil.ignore_patterns("cache"))
    monkeypatch.chdir(tmp_path / "lbo")
    runner = CliRunner()

    def fm(*args):
        result = runner.invoke(app, ["--json", *args], catch_exceptions=False)
        return json.loads(result.stdout), result.exit_code

    data, code = fm("list", "metrics")
    assert code == 0 and len(data["data"]) > 80
    data, _ = fm("show", "ebit")
    assert data["data"]["depends_on"] == ["ebitda", "depreciation_amortization"]
    assert fm("metric", "set", "interest_rate", "--value", "0.05")[1] == 0
    assert fm("metric", "set", "ebit", "--formula", "ebitda - depreciation_amortization")[1] == 0
    data, _ = fm("trace", "ebit", "--upstream", "--depth", "1")
    assert [m["name"] for m in data["data"]["metrics"]] == ["ebitda", "depreciation_amortization"]
    assert fm("validate")[1] == 0
    data, code = fm("run", "--set", "interest_rate=0.08", "--output", "equity_irr")
    high = data["data"]["values"]["equity_irr"]["value"]
    data, _ = fm("run", "--output", "equity_irr")
    assert code == 0 and high < data["data"]["values"]["equity_irr"]["value"]
    data, code = fm("sweep", "--input", "interest_rate=0.04,0.06,0.08", "--output", "moic_3y")
    assert code == 0 and len(data["data"]["rows"]) == 3
    data, code = fm("analysis", "run", "interest_sensitivity")
    assert code == 0 and data["data"]["chart"]["kind"] == "line"
    data, code = fm("metric", "delete", "tax_rate")
    assert code == 1 and data["errors"][0]["code"] == "in_use"


def test_sweeps_share_one_engine_and_match_full_runs(workspace, monkeypatch):
    """Scenarios reuse one overridden engine and leave the shared cache as it was."""
    from epm.engine import Engine

    workspace.run()
    cached = len(workspace.cache)
    built = []
    init = Engine.__init__

    def counting_init(self, *args, **kwargs):
        built.append(self)
        init(self, *args, **kwargs)

    monkeypatch.setattr(Engine, "__init__", counting_init)
    outputs = ["equity_irr", "moic", "tlb_close@2027"]
    sweep = workspace.sweep(
        {"interest_rate": list(np.linspace(0.03, 0.09, 40)), "tlb_leverage": [3.0, 3.5, 4.0]},
        outputs,
    )
    monkeypatch.undo()
    assert sweep.ok and len(sweep.rows) == 120
    assert len(built) == 1
    assert len(workspace.cache) == cached
    for row in sweep.rows[::23]:
        run = workspace.engine.run(row["inputs"], outputs=["equity_irr", "moic", "tlb_close"])
        assert row["outputs"]["equity_irr"] == run["equity_irr"]
        assert row["outputs"]["moic"] == run["moic"]
        assert row["outputs"]["tlb_close@2027"] == run["tlb_close"].loc[2027]
