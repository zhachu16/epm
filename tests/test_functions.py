import pytest

from epm import Engine, Workspace, load_model
from epm.functions import FunctionCallError, FunctionRuntime, Limits, discover

YEARS = [2025, 2026, 2027, 2028]

SOURCE = '''
from epm import function
import numpy as np
import pandas as pd


@function
def straight_line(principal, annual_pct, periods=None):
    """Mandatory amortization: a fixed share of the original principal each year."""
    n = 4 if periods is None else int(periods)
    return np.full(n, principal * annual_pct)


@function(returns="scalar")
def moic(invested, returned):
    return returned / invested


@function
def doubled(x):
    return x * 2


@function(causal=True)
def running(x):
    return x.cumsum()


@function
def broken(x):
    return 1 / 0


@function
def wrong_length(x):
    return [1, 2]
'''


@pytest.fixture(scope="module")
def runtime():
    rt = FunctionRuntime(SOURCE)
    yield rt
    rt.close()


def make(metrics, runtime, source=SOURCE):
    model = load_model(
        {"settings": {"default_axis": "fy"}, "axes": {"fy": {"values": YEARS}}, "metrics": metrics}
    )
    return Engine(model, functions=source, runtime=runtime)


def codes(engine):
    return {d.object: d.code for d in engine.diagnostics}


def test_discovery():
    specs, errors = discover(SOURCE)
    assert errors == []
    sl = specs["straight_line"]
    assert sl.params == ("principal", "annual_pct", "periods")
    assert sl.required == {"principal", "annual_pct"}
    assert (sl.returns, sl.causal) == ("series", False)
    assert specs["moic"].returns == "scalar" and specs["running"].causal
    assert sl.source.startswith("@function\ndef straight_line")

    _, errors = discover("from epm import function\n@function\ndef f(*xs): return 1\n")
    assert "named parameters only" in errors[0].message
    _, errors = discover("@function(speed=1)\ndef f(x): return x\n")
    assert "unknown @function option" in errors[0].message
    _, errors = discover("def broken(:\n")
    assert "syntax error" in errors[0].message


def test_series_and_scalar_functions(runtime):
    e = make(
        {
            "principal": {"value": 400},
            "amort": {"formula": "straight_line(principal, 0.05)"},
            "balance": {"formula": "principal - cumsum(amort)"},
            "invested": {"value": 100},
            "returned": {"value": 250},
            "multiple": {"formula": "moic(invested, returned=returned)"},
            "twice": {"formula": "doubled(balance) + doubled(balance)"},
        },
        runtime,
    )
    assert e.diagnostics == []
    assert e.shape("amort") == "fy" and e.shape("multiple") is None
    r = e.run()
    assert r.ok, [d.to_dict() for d in r.diagnostics]
    assert list(r["amort"]) == [20] * 4
    assert list(r["balance"]) == [380, 360, 340, 320]
    assert r["multiple"] == 2.5
    assert list(r["twice"]) == [1520, 1440, 1360, 1280]


def test_identical_calls_run_once(runtime):
    e = make(
        {
            "a": {"axis": "fy", "value": 3},
            "x": {"formula": "doubled(a)"},
            "y": {"formula": "doubled(a) + 1"},
        },
        runtime,
    )
    before = runtime.calls
    r = e.run()
    assert list(r["y"]) == [7] * 4
    assert runtime.calls - before <= 1


def test_argument_errors(runtime):
    e = make(
        {
            "a": {"value": 1},
            "too_many": {"formula": "moic(a, a, a)"},
            "unknown_kw": {"formula": "moic(a, b=a)"},
            "missing": {"formula": "straight_line(a)"},
            "typo": {"formula": "straight_lin(a, a)"},
        },
        runtime,
    )
    assert codes(e) == {
        "metric:too_many": "invalid_argument",
        "metric:unknown_kw": "invalid_argument",
        "metric:missing": "invalid_argument",
        "metric:typo": "unknown_function",
    }
    assert "straight_line" in next(d for d in e.diagnostics if d.object == "metric:typo").message


def test_runtime_errors_are_reported_and_block_dependants(runtime):
    e = make(
        {
            "a": {"axis": "fy", "value": 1},
            "b": {"formula": "broken(a)"},
            "c": {"formula": "b + 1"},
            "d": {"formula": "wrong_length(a)"},
        },
        runtime,
    )
    r = e.run()
    assert r.status["b"].state == "error"
    assert "ZeroDivisionError" in r.status["b"].message
    assert "functions.py line" in r.status["b"].message
    assert r.status["c"].state == "blocked"
    assert "returned 2 values" in r.status["d"].message


def test_causality_controls_use_in_recurrences(runtime):
    e = make(
        {
            "flow": {"axis": "fy", "value": 1},
            "x": {"formula": "prev(x, init=0) + doubled(flow) + doubled(prev(x, init=0))"},
        },
        runtime,
    )
    assert codes(e) == {"metric:x": "circular_reference"}  # reads all of x, including now
    e = make(
        {
            "flow": {"axis": "fy", "value": 1},
            "level": {"formula": "prev(level, init=0) + flow"},
            "acc": {"formula": "running(prev(level, init=0))"},
            "level2": {"formula": "prev(level2, init=0) + acc"},
        },
        runtime,
    )
    assert e.diagnostics == []
    assert list(e.run()["acc"]) == [0, 1, 3, 6]


def test_name_collisions(runtime):
    e = make({"doubled": {"value": 1}}, runtime)
    assert codes(e)["function:doubled"] == "function_error"
    e = make(
        {"x": {"value": 1}},
        runtime,
        source="from epm import function\n@function\ndef total(x): return x\n",
    )
    assert "built-in" in e.diagnostics[0].message


def test_limits_and_import_guard():
    source = """
from epm import function

@function
def spin(x):
    while True:
        pass

@function
def sneaky(x):
    import subprocess
    return x
"""
    rt = FunctionRuntime(source, Limits(timeout=1.5, cpu_seconds=5))
    try:
        with pytest.raises(FunctionCallError) as e:
            rt.call("spin", [1.0], {})
        assert e.value.code == "timeout"
        with pytest.raises(FunctionCallError, match="import of 'subprocess' is not allowed"):
            rt.call("sneaky", [1.0], {})  # the worker restarted after the timeout
    finally:
        rt.close()


def test_close_after_the_worker_died():
    # Ctrl+C or a SIGTERM to the process group stops the worker before the runtime closes it
    rt = FunctionRuntime(SOURCE)
    assert rt.call("moic", [100.0, 250.0], {}) == 2.5
    rt._proc.kill()
    rt._proc.wait()
    rt.close()


def test_prestarted_workers_finish_loading_on_the_first_call():
    rt = FunctionRuntime(SOURCE)
    try:
        rt.prestart()
        rt.prestart()  # already started: no second worker
        assert rt.call("moic", [100.0, 300.0], {}) == 3.0 and rt.calls == 1
    finally:
        rt.close()
    broken = FunctionRuntime("raise RuntimeError('boom')\n")
    broken.prestart()
    with pytest.raises(FunctionCallError, match="failed to load: RuntimeError: boom"):
        broken.call("x", [], {})


def test_load_errors_are_reported():
    rt = FunctionRuntime("raise RuntimeError('boom')\n")
    with pytest.raises(FunctionCallError, match="failed to load: RuntimeError: boom"):
        rt.call("x", [], {})


# -- editing functions ------------------------------------------------------------------

CODE_V1 = """@function
def fee(amount, rate=0.02):
    return amount * rate
"""

CODE_V2 = """@function
def fee(amount, rate=0.03):
    return amount * rate
"""


def test_function_ops_and_fingerprints():
    ws = Workspace(
        load_model(
            {
                "settings": {"default_axis": "fy"},
                "axes": {"fy": {"values": YEARS}},
                "metrics": {"debt": {"axis": "fy", "value": 100}},
            }
        )
    )
    try:
        r = ws.apply(
            [
                {"op": "create", "kind": "function", "name": "fee", "def": {"code": CODE_V1}},
                {"op": "create", "kind": "metric", "name": "fees", "def": {"formula": "fee(debt)"}},
            ]
        )
        assert r.ok, r.to_dict()
        assert ws.functions.startswith("from epm import function\n")
        assert [c.change for c in r.changes if c.kind == "function"] == ["added"]
        assert list(ws.run()["fees"]) == [2.0] * 4

        r = ws.apply(
            [{"op": "update", "kind": "function", "name": "fee", "set": {"code": CODE_V2}}]
        )
        assert r.ok
        run = ws.run()
        assert list(run["fees"]) == [3.0] * 4
        assert "debt" in run.reused  # a function change only recomputes its callers
        assert r.impact.outputs["fees"]["delta"] == {str(y): 1.0 for y in YEARS}

        r = ws.apply([{"op": "rename", "kind": "function", "name": "fee", "to": "arrangement_fee"}])
        assert r.ok and ws.model.metrics["fees"].formula == "arrangement_fee(debt)"
        assert "def arrangement_fee(amount" in ws.functions

        r = ws.apply([{"op": "delete", "kind": "function", "name": "arrangement_fee"}])
        assert not r.ok and r.errors[0].code == "in_use"

        bad = ws.apply(
            [
                {
                    "op": "create",
                    "kind": "function",
                    "name": "g",
                    "def": {"code": "def g(x): return x"},
                }
            ]
        )
        assert not bad.ok and "decorated with @function" in bad.errors[0].message

        ws.undo()
        ws.undo()
        assert "rate=0.02" in ws.functions
        assert list(ws.run()["fees"]) == [2.0] * 4
        ws.undo()
        assert ws.functions is None or "fee" not in ws.functions
    finally:
        ws.close()
