import numpy as np
import pytest

from epm import Engine, load_model

YEARS = [2025, 2026, 2027, 2028, 2029]


def make(metrics, circularities=None, axes=None, checks=None):
    return Engine(
        load_model(
            {
                "settings": {"default_axis": "fy"},
                "axes": axes or {"fy": {"values": YEARS}},
                "metrics": metrics,
                "circularities": circularities or {},
                "checks": checks or {},
            }
        )
    )


FEES = {
    "price": {"value": 1000},
    "uses": {"formula": "price + fees"},
    "debt": {"formula": "0.6 * uses"},
    "fees": {"formula": "0.02 * debt"},
    "equity": {"formula": "uses - debt"},
}


def test_scalar_loop_is_solved_when_declared():
    e = make(FEES, {"financing_fees": {"metrics": ["uses", "debt", "fees"]}})
    assert e.diagnostics == []
    [step] = [s for s in e.steps if s.kind == "circular"]
    assert set(step.members) == {"uses", "debt", "fees"}
    r = e.run()
    assert r.ok
    assert r["uses"] == pytest.approx(1000 / (1 - 0.6 * 0.02), abs=1e-9)
    assert r["equity"] == pytest.approx(0.4 * r["uses"])
    stats = r.solver["financing_fees"]
    assert stats.converged and 2 < stats.iterations < 20
    assert r.status["fees"].message.startswith("solved by circularity 'financing_fees'")
    assert r.to_dict()["solver"]["financing_fees"]["converged"] is True


def test_undeclared_loop_is_rejected_with_hint():
    e = make(FEES)
    errors = {d.object for d in e.diagnostics if d.code == "circular_reference"}
    assert errors == {"metric:uses", "metric:debt", "metric:fees"}
    assert "declare it under 'circularities'" in e.diagnostics[0].message
    assert e.run().status["equity"].state == "blocked"


def test_partially_declared_loop_names_the_missing_metric():
    e = make(FEES, {"c": {"metrics": ["uses", "debt"]}})
    assert "fees not in circularity 'c'" in e.diagnostics[0].message


def test_circularity_validation():
    e = make(
        {**FEES, "a": {"value": 1}},
        {
            "c": {"metrics": ["uses", "debt", "fees"]},
            "d": {"metrics": ["fees", "nope"]},
            "idle": {"metrics": ["a"]},
        },
    )
    found = {(d.object, d.code) for d in e.diagnostics}
    assert ("circularity:d", "unknown_reference") in found
    assert ("circularity:d", "duplicate_member") in found
    assert ("circularity:idle", "unused_circularity") in found


def reference_debt_paydown(ebitda, rate, opening):
    """Closed form for interest on the average balance with a full cash sweep."""
    debt, out = opening, []
    for e in ebitda:
        interest = rate * (2 * debt - e) / (2 - rate)
        sweep = e - interest
        assert 0 < sweep < debt  # the closed form assumes the sweep is not capped
        debt -= sweep
        out.append((interest, debt))
    return out


def test_average_balance_interest_with_cash_sweep():
    ebitda = [60, 65, 70, 75, 80]
    metrics = {
        "rate": {"value": 0.08},
        "opening_debt": {"value": 600},
        "ebitda": {"value": dict(zip(map(str, YEARS), ebitda, strict=True))},
        "debt_open": {"formula": "prev(debt, init=opening_debt)"},
        "interest": {"formula": "rate * (debt_open + debt) / 2"},
        "cash_for_sweep": {"formula": "ebitda - interest"},
        "sweep": {"formula": "clip(min(cash_for_sweep, debt_open), lower=0)"},
        "debt": {"formula": "debt_open - sweep"},
        "total_interest": {"formula": "total(interest)"},
    }
    e = make(
        metrics, {"avg_interest": {"metrics": ["interest", "cash_for_sweep", "sweep", "debt"]}}
    )
    assert e.diagnostics == []
    [step] = [s for s in e.steps if s.kind == "recurrence"]
    assert [b.circularity for b in step.blocks] == [None, "avg_interest"]
    r = e.run()
    assert r.ok
    ref = reference_debt_paydown(ebitda, 0.08, 600)
    np.testing.assert_allclose(r["interest"], [i for i, _ in ref], rtol=1e-10)
    np.testing.assert_allclose(r["debt"], [d for _, d in ref], rtol=1e-10)
    stats = r.solver["avg_interest"]
    assert stats.converged and set(stats.periods) == set(map(str, YEARS))
    assert r["total_interest"] == pytest.approx(sum(i for i, _ in ref))


def test_divergence_and_oscillation():
    diverge = make(
        {"x": {"formula": "2 * y + 1"}, "y": {"formula": "x"}, "z": {"formula": "x + 1"}},
        {"c": {"metrics": ["x", "y"], "max_iter": 2000}},
    )
    r = diverge.run()
    assert r.status["x"].state == "error" and r.status["z"].state == "blocked"
    assert not r.solver["c"].converged
    assert [d.code for d in r.diagnostics] == ["not_converged"]

    loop = {"x": {"formula": "1 - y"}, "y": {"formula": "x"}}
    r = make(loop, {"c": {"metrics": ["x", "y"], "max_iter": 50}}).run()
    assert r.status["x"].state == "error"
    assert "did not converge in 50 iterations" in r.status["x"].message
    r = make(loop, {"c": {"metrics": ["x", "y"], "damping": 0.5}}).run()
    assert r["x"] == pytest.approx(0.5)


def test_mixed_scalar_and_series_block():
    e = make(
        {
            "base": {"axis": "fy", "value": 100},
            "fee_rate": {"value": 0.01},
            "fee": {"formula": "fee_rate * total(gross)"},
            "gross": {"formula": "base + fee"},
        },
        {"fees": {"metrics": ["fee", "gross"]}},
    )
    r = e.run()
    assert r.ok
    # fee = 0.01 * 5 * (100 + fee)  =>  fee = 5 / 0.95
    assert r["fee"] == pytest.approx(5 / 0.95)
    np.testing.assert_allclose(r["gross"], [100 + 5 / 0.95] * 5)


# -- forecast-only metrics ----------------------------------------------------------------

AXES = {"fy": {"values": [2023, 2024, 2025, 2026, 2027], "historical": [2023, 2024]}}


def test_forecast_scope_prev_and_reductions():
    e = make(
        {
            "flow": {"axis": "fy", "value": 10},
            "debt": {"scope": "forecast", "formula": "prev(debt, init=100) - flow"},
            "interest": {"formula": "0.1 * prev(debt, init=100)"},
            "cf": {"formula": "where(period() == 2025, -100, 60)", "scope": "forecast"},
            "ret": {"formula": "irr(cf)"},
            "tot": {"formula": "total(debt)"},
            "everywhere": {"formula": "flow * 2"},
        },
        axes=AXES,
    )
    assert e.diagnostics == []
    r = e.run()
    assert r.ok, [d.to_dict() for d in r.diagnostics]
    assert list(r["debt"].iloc[2:]) == [90, 80, 70]
    assert r["debt"].iloc[:2].isna().all()
    assert e.metrics["interest"].start == 2  # inferred from debt
    assert list(r["interest"].iloc[2:]) == [10, 9, 8]
    assert r["tot"] == 240
    # -100 + 60x + 60x^2 = 0 with x = 1 / (1 + r): only forecast periods count
    x = (-1 + np.sqrt(1 + 20 / 3)) / 2
    assert r["ret"] == pytest.approx(1 / x - 1, abs=1e-9)
    assert list(r["everywhere"]) == [20] * 5
    assert r.status["debt"].state == "ok"  # blank history is not a missing value


def test_checks_ignore_periods_out_of_scope():
    e = make(
        {
            "a": {"axis": "fy", "scope": "forecast", "value": 5},
            "b": {"axis": "fy", "scope": "forecast", "value": 5},
        },
        axes=AXES,
        checks={"same": {"expr": "a - b"}},
    )
    assert e.run().checks["same"].passed


def test_scope_with_history_is_rejected():
    with pytest.raises(ValueError, match="forecast-only metric cannot have 'history'"):
        load_model(
            {
                "metrics": {
                    "x": {
                        "scope": "forecast",
                        "formula": "1",
                        "history": {"source": "s", "item": "x"},
                    }
                }
            }
        )


def test_override_keeps_forecast_scope():
    e = make(
        {"d": {"scope": "forecast", "axis": "fy", "value": 1}, "x": {"formula": "d * 2"}},
        axes=AXES,
    )
    r = e.run(overrides={"d": 3})
    assert list(r["x"].iloc[2:]) == [6, 6, 6] and r["x"].iloc[:2].isna().all()
