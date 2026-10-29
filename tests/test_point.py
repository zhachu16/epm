"""Period-by-period evaluation must agree exactly with whole-series evaluation."""

import math

import numpy as np
import pandas as pd
import pytest

from epm import Workspace, load_model
from epm.formula import AxisInfo, FormulaEnv, compile_formula, parse_formula
from epm.formula.point import Frame, compile_point

INDEX = pd.Index([2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030], name="fy")
HISTORICAL = np.array([True, True] + [False] * 8)
AXES = {"fy": AxisInfo("fy", "numeric", INDEX, HISTORICAL)}
NAN, INF = math.nan, math.inf

MEMBERS = {
    "a": [1.5, -2.0, 0.0, -0.0, NAN, 3.0, INF, -4.25, 1e300, 0.5],
    "b": [0.0, 2.0, -0.0, 3.0, 1.0, NAN, 2.0, -INF, 1e10, -0.5],
}
UPSTREAM = {
    "u": pd.Series([2.0, 0.0, -1.0, 5.0, NAN, 1.0, 0.25, 3.0, -2.0, 4.0], index=INDEX),
    "flag": pd.Series([True, False, True, True, False, False, True, False, True, True], INDEX),
    "k": np.float64(0.07),
}

FORMULAS = [
    "a + b * 2",
    "a - b",
    "a / b",
    "a // b",
    "a % b",
    "a ** 0.5",
    "b ** -1",
    "a * b + u",
    "-a",
    "+a",
    "not a",
    "a > b",
    "a <= u",
    "a == b",
    "(a > 0) and (b < 1)",
    "a or b",
    "(a > b) + (b > a)",
    "prev(a)",
    "prev(a, 2, init=5)",
    "prev(a, 0)",
    "prev(a + u, init=k)",
    "prev(prev(b), init=u)",
    "cumsum(a)",
    "cumprod(b)",
    "cumsum(a * flag)",
    "where(a > 0, a, b)",
    "where(flag, a, 0)",
    "where(a, 1, 2)",
    "abs(a)",
    "abs(a > b)",
    "round(a, 2)",
    "clip(a, 0, 1)",
    "clip(a, lower=u)",
    "min(a, b)",
    "max(a, 0, b)",
    "max(a, u)",
    "max(a > 0, b)",
    "pmt(0.05, 10, a)",
    "pmt(b, 10, 100)",
    "a + total(u)",
    "a * period_index()",
    "a + is_forecast()",
    "where(is_forecast(), a, u)",
    "a * k + u",
    "a + period()",
]


def _env(starts=None) -> FormulaEnv:
    return FormulaEnv(AXES, metric_axis="fy", default_axis="fy", starts=starts or {})


def _series_values():
    members = {m: pd.Series(v, index=INDEX) for m, v in MEMBERS.items()}
    return {**UPSTREAM, **members}


def _compare(formula: str, env: FormulaEnv) -> None:
    node = parse_formula(formula)
    with np.errstate(all="ignore"):
        raw = compile_formula(node, env)(_series_values())
        if isinstance(raw, pd.Series):
            expected = raw.reindex(INDEX).to_numpy(dtype=float)
        else:
            expected = np.full(len(INDEX), float(raw))
        frame = Frame(UPSTREAM, {m: list(v) for m, v in MEMBERS.items()}, INDEX)
        at = compile_point(node, env, frozenset(MEMBERS))(frame)
        got = np.array([float(at(t)) for t in range(len(INDEX))])
    np.testing.assert_array_equal(got, expected, err_msg=formula)


@pytest.mark.parametrize("formula", FORMULAS)
def test_point_matches_series(formula):
    _compare(formula, _env())


@pytest.mark.parametrize("formula", ["prev(a)", "prev(a, 2, init=u)", "cumsum(a)", "cumprod(b)"])
def test_point_matches_series_for_forecast_only_arguments(formula):
    _compare(formula, _env(starts={"a": 2, "b": 3}))


def test_recurrences_with_custom_functions_fall_back_to_series_evaluation():
    model = load_model(
        {
            "settings": {"default_axis": "fy"},
            "axes": {"fy": {"values": [1, 2, 3, 4]}},
            "metrics": {
                "x": {"formula": "prev(y, init=8) + 1"},
                "y": {"formula": "damp(x)"},
            },
        }
    )
    functions = "from epm import function\n\n@function(causal=True)\ndef damp(x):\n    return x / 2"
    ws = Workspace(model, functions=functions)
    try:
        run = ws.run()
        assert list(run.values["x"]) == [9.0, 5.5, 3.75, 2.875]
        assert list(run.values["y"]) == [4.5, 2.75, 1.875, 1.4375]
    finally:
        ws.close()
