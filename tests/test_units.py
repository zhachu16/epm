from epm import Engine, load_model


def make(metrics, checks=None, **settings):
    return Engine(
        load_model(
            {
                "settings": settings,
                "axes": {"fy": {"values": [1, 2]}},
                "metrics": metrics,
                "checks": checks or {},
            }
        )
    )


def warnings(engine):
    return [(d.object, d.message) for d in engine.diagnostics if d.code == "unit_mismatch"]


BASE = {
    "revenue": {"axis": "fy", "value": 100, "unit": "USD m"},
    "margin": {"value": 0.2, "unit": "%"},
    "multiple": {"value": 8, "unit": "x"},
}


def test_adding_different_units_warns():
    e = make({**BASE, "bad": {"formula": "revenue + margin"}})
    assert warnings(e) == [("metric:bad", "'+' adds 'USD m' and '%'")]
    assert e.run().status["bad"].state == "ok"  # a warning, not an error


def test_multiplication_is_not_checked_and_literals_are_unitless():
    e = make(
        {
            **BASE,
            "ebitda": {"formula": "revenue * margin"},
            "capped": {"formula": "max(revenue, 0) - 1"},
            "ev": {"formula": "multiple * 10"},
        }
    )
    assert warnings(e) == []
    assert e.unit("capped") == "USD m"
    assert e.unit("ebitda") is None


def test_units_are_inferred_through_chains_and_compared_to_declared():
    e = make(
        {
            **BASE,
            "a": {"formula": "revenue - 5"},
            "b": {"formula": "prev(a, init=0) + a"},
            "c": {"formula": "b", "unit": "%"},
        }
    )
    assert e.unit("b") == "USD m"
    assert warnings(e) == [("metric:c", "formula gives 'USD m' but the metric's unit is '%'")]


def test_comparison_where_and_init():
    e = make(
        {
            **BASE,
            "cmp": {"formula": "where(revenue > margin, 1, 0)"},
            "branch": {"formula": "where(revenue > 0, revenue, margin)"},
            "lagged": {"axis": "fy", "formula": "prev(revenue, init=margin)"},
        }
    )
    assert warnings(e) == [
        ("metric:cmp", "'>' compares 'USD m' and '%'"),
        ("metric:branch", "where() mixes 'USD m' and '%'"),
        ("metric:lagged", "prev() mixes 'USD m' and '%'"),
    ]


def test_unit_spelling_is_normalized():
    e = make({**BASE, "x": {"formula": "revenue + y"}, "y": {"value": 1, "unit": " usd  M "}})
    assert warnings(e) == []


def test_mixed_units_report_once():
    e = make(
        {**BASE, "bad": {"formula": "revenue + margin"}, "worse": {"formula": "bad + revenue"}}
    )
    assert [obj for obj, _ in warnings(e)] == ["metric:bad"]


def test_strict_units_make_mismatches_errors():
    e = make({**BASE, "bad": {"formula": "revenue + margin"}}, strict_units=True)
    [d] = e.diagnostics
    assert (d.severity, d.code) == ("error", "unit_mismatch")
    assert e.run().status["bad"].state == "error"


def test_checks_are_unit_checked():
    e = make(BASE, checks={"c": {"expr": "revenue - margin"}})
    assert warnings(e) == [("check:c", "'-' subtracts 'USD m' and '%'")]
