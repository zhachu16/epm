import numpy as np
import pandas as pd
import pytest

from epm import Engine, EpmError, load_model


def make(metrics, **extra) -> Engine:
    model = {
        "settings": {"default_axis": "fy"},
        "axes": {"fy": {"values": [2024, 2025, 2026, 2027], "historical": [2024]}},
        "metrics": metrics,
        **extra,
    }
    return Engine(load_model(model))


def codes(engine: Engine) -> dict[str, str]:
    return {d.object: d.code for d in engine.diagnostics}


def test_scalar_broadcasting_and_per_period_values():
    e = make(
        {
            "base": {"value": 2},
            "units": {"value": {"2024": 1, "2025": 2, "2026": 3}},
            "x": {"formula": "units * base"},
        }
    )
    r = e.run()
    assert e.shape("x") == "fy"
    assert list(r["x"])[:3] == [2, 4, 6]
    assert np.isnan(r["x"].iloc[3])
    assert r.status["units"].state == "warning"
    assert r.status["x"].state == "warning"
    # only the origin of the missing value is reported
    assert [d.object for d in r.diagnostics if d.code == "missing_value"] == ["metric:units"]


def test_declared_axis_broadcasts_scalar_formula():
    e = make({"rate": {"value": 0.1}, "r": {"axis": "fy", "formula": "rate * 2"}})
    assert list(e.run()["r"]) == [0.2] * 4


def test_axis_mismatch():
    e = make(
        {
            "a": {"axis": "fy", "value": 1},
            "b": {"axis": "asset", "value": 2},
            "c": {"formula": "a + b"},
        },
        axes={
            "fy": {"values": [2024, 2025]},
            "asset": {"kind": "category", "values": ["x", "y"]},
        },
    )
    assert codes(e) == {"metric:c": "axis_mismatch"}
    r = e.run()
    assert r.status["c"].state == "error"
    assert r.status["a"].state == "ok"


def test_declared_axis_conflict():
    e = make(
        {"a": {"axis": "fy", "value": 1}, "b": {"axis": "asset", "formula": "a * 2"}},
        axes={"fy": {"values": [1, 2]}, "asset": {"kind": "category", "values": ["x"]}},
    )
    assert codes(e) == {"metric:b": "axis_mismatch"}


def test_unknown_reference_suggests_and_blocks_downstream():
    e = make(
        {"revenue": {"value": 1}, "ebit": {"formula": "revenu * 2"}, "y": {"formula": "ebit + 1"}}
    )
    [d] = e.diagnostics
    assert d.code == "unknown_reference"
    assert d.detail["suggestions"] == ["revenue"]
    r = e.run()
    assert r.status["ebit"].state == "error"
    assert r.status["y"].state == "blocked"
    assert not r.ok


def test_same_period_cycle_is_rejected_with_path():
    e = make(
        {
            "a": {"formula": "b + 1"},
            "b": {"formula": "c * 2"},
            "c": {"formula": "a - 1"},
            "d": {"value": 1},
        }
    )
    errors = [d for d in e.diagnostics if d.code == "circular_reference"]
    assert {d.object for d in errors} == {"metric:a", "metric:b", "metric:c"}
    path = errors[0].detail["path"]
    assert path[0] == path[-1] and len(path) == 4
    r = e.run()
    assert r.status["d"].state == "ok"
    assert r.status["a"].state == "error"


def test_self_reference_hint():
    e = make({"x": {"formula": "x + 1"}})
    [d] = e.diagnostics
    assert d.code == "circular_reference"
    assert "prev(x)" in d.message


def test_recurrence_with_reduction_is_rejected():
    # y needs every period of x, but x needs last period's y: not steppable by period
    e = make({"x": {"formula": "prev(y, init=0) + 1"}, "y": {"formula": "x + total(x)"}})
    assert {d.code for d in e.diagnostics} == {"noncausal_recurrence"}
    # with no lag anywhere in the loop it is simply circular
    e = make({"x": {"formula": "1 + total(y)"}, "y": {"formula": "x * 2"}})
    assert {d.code for d in e.diagnostics} == {"circular_reference"}


def test_self_recurrence_takes_the_default_axis():
    e = make({"x": {"formula": "prev(x, init=1) * 2"}})
    assert e.diagnostics == [] and e.shape("x") == "fy"
    assert list(e.run()["x"]) == [2, 4, 8, 16]
    # without a default axis there is nothing to step through
    no_default = Engine(load_model({"metrics": {"x": {"formula": "prev(x, init=1)"}}}))
    [d] = no_default.diagnostics
    assert d.code == "recurrence_axis" and "declare an axis" in d.message


def test_prev_lag_and_missing_init():
    e = make(
        {
            "s": {"value": {"2024": 1, "2025": 2, "2026": 3, "2027": 4}},
            "p2": {"formula": "prev(s, lag=2, init=0)"},
            "p": {"formula": "prev(s)"},
            "n": {"formula": "next(s, fill=-1)"},
        }
    )
    r = e.run()
    assert list(r["p2"]) == [0, 0, 1, 2]
    assert np.isnan(r["p"].iloc[0]) and list(r["p"].iloc[1:]) == [1, 2, 3]
    assert list(r["n"]) == [2, 3, 4, -1]


def test_builtins():
    e = make(
        {
            "s": {"value": {"2024": -1, "2025": 2, "2026": 3, "2027": 4}},
            "w": {"formula": "where(is_forecast(), s, 0)"},
            "tern": {"formula": "s if s > 2 else -s"},
            "c": {"formula": "clip(s, lower=0, upper=3)"},
            "mx": {"formula": "max(s, 2.5)"},
            "peak": {"formula": "max(s)"},
            "cs": {"formula": "cumsum(s)"},
            "t": {"formula": "total(s)"},
            "a": {"formula": "at(s, 2026)"},
            "yr": {"formula": "period()"},
            "i": {"formula": "period_index()"},
            "flag": {"formula": "(s > 0) & (s < 4)"},
            "cf": {"value": {"2024": -100, "2025": 10, "2026": 10, "2027": 110}},
            "ret": {"formula": "irr(cf)"},
            "pv": {"formula": "npv(0.1, cf)"},
            "pay": {"formula": "pmt(0.05, 10, 1000)"},
        }
    )
    assert e.diagnostics == []
    r = e.run()
    assert list(r["w"]) == [0, 2, 3, 4]
    assert list(r["tern"]) == [1, -2, 3, 4]
    assert list(r["c"]) == [0, 2, 3, 3]
    assert list(r["mx"]) == [2.5, 2.5, 3, 4]
    assert r["peak"] == 4
    assert list(r["cs"]) == [-1, 1, 4, 8]
    assert r["t"] == 8
    assert r["a"] == 3
    assert list(r["yr"]) == [2024, 2025, 2026, 2027]
    assert list(r["i"]) == [0, 1, 2, 3]
    assert list(r["flag"]) == [False, True, True, False]
    assert r["ret"] == pytest.approx(0.10)
    assert r["pv"] == pytest.approx(0.0, abs=1e-9)
    assert r["pay"] == pytest.approx(-129.5045749654567)


def test_at_period_must_exist():
    e = make({"s": {"axis": "fy", "value": 1}, "a": {"formula": "at(s, 1999)"}})
    assert codes(e) == {"metric:a": "invalid_argument"}


def test_npv_rate_must_be_scalar():
    e = make(
        {
            "r": {"axis": "fy", "value": 0.1},
            "s": {"axis": "fy", "value": 1},
            "v": {"formula": "npv(r, s)"},
        }
    )
    assert codes(e) == {"metric:v": "axis_mismatch"}


def test_division_by_zero_is_a_warning_not_a_crash():
    e = make({"zero": {"value": 0}, "x": {"formula": "1 / zero"}, "y": {"formula": "x + 1"}})
    r = e.run()
    assert np.isinf(r["x"])
    assert r.status["x"].state == "warning"
    assert [d.code for d in r.diagnostics] == ["nonfinite_value"]


def test_override_scalar_on_series_input_keeps_axis():
    e = make({"g": {"axis": "fy", "value": 0.1}, "x": {"formula": "g * 10"}})
    r = e.run(overrides={"g": 0.2})
    assert list(r["x"]) == [2.0] * 4
    r = e.run(overrides={"g": pd.Series([1.0, 2.0, 3.0, 4.0], index=[2024, 2025, 2026, 2027])})
    assert list(r["x"]) == [10, 20, 30, 40]


def test_unknown_output_or_override_raises():
    e = make({"a": {"value": 1}})
    with pytest.raises(EpmError) as err:
        e.run(outputs=["b"])
    assert err.value.code == "unknown_metric"
    with pytest.raises(EpmError):
        e.run(overrides={"nope": 1})


def test_reserved_and_invalid_names():
    with pytest.raises(ValueError, match="reserved"):
        make({"total": {"value": 1}})
    with pytest.raises(ValueError, match="must start with"):
        make({"2x": {"value": 1}})


def test_metric_needs_exactly_one_definition():
    with pytest.raises(ValueError, match="exactly one"):
        make({"a": {"value": 1, "formula": "2"}})
    with pytest.raises(ValueError, match="exactly one"):
        make({"a": {"unit": "x"}})


def test_table_references_are_validated():
    e = make({"a": {"value": 1}}, tables={"t": {"rows": [{"metric": "a"}, {"metric": "b"}]}})
    assert codes(e) == {"table:t": "unknown_reference"}


def test_result_to_dict_is_json_safe():
    import json

    e = make({"s": {"value": {"2024": 1}}, "z": {"value": 0}, "inf": {"formula": "1 / z"}})
    data = e.run().to_dict()
    json.dumps(data, allow_nan=False)
    assert data["values"]["s"]["values"]["2025"] is None
    assert data["values"]["inf"] == {"axis": None, "value": None}
