import pytest

from fm.errors import FormulaError
from fm.formula import FULL, parse_formula, references
from fm.formula.builtins import irr


@pytest.mark.parametrize(
    ("formula", "code"),
    [
        ("revenue.sum()", "disallowed_syntax"),
        ("revenue[0]", "disallowed_syntax"),
        ("lambda: 1", "disallowed_syntax"),
        ("__import__('os')", "unknown_function"),
        ("[a for a in b]", "disallowed_syntax"),
        ("a +", "syntax_error"),
        ("", "syntax_error"),
        ("'text' + 1", "disallowed_syntax"),
        ("open('x')", "unknown_function"),
        ("prev(x, lag=-1)", "invalid_argument"),
        ("prev(x, lag=y)", "invalid_argument"),
        ("prev(x, bogus=1)", "invalid_argument"),
        ("where(a, b)", "invalid_argument"),
        ("min()", "invalid_argument"),
        ("is_forecast(fy)", "invalid_argument"),
    ],
)
def test_rejects(formula, code):
    with pytest.raises(FormulaError) as e:
        parse_formula(formula)
    assert e.value.code == code


def test_unknown_function_suggests():
    with pytest.raises(FormulaError) as e:
        parse_formula("prevv(x)")
    assert "prev" in e.value.detail["suggestions"]


def test_references_and_lags():
    assert references(parse_formula("a + b * a")) == {"a": 0, "b": 0}
    assert references(parse_formula("prev(a)")) == {"a": 1}
    assert references(parse_formula("prev(prev(a))")) == {"a": 2}
    assert references(parse_formula("prev(a, lag=3, init=b)")) == {"a": 3, "b": 0}
    assert references(parse_formula("total(a)")) == {"a": FULL}
    assert references(parse_formula("max(a)")) == {"a": FULL}
    assert references(parse_formula("max(a, b)")) == {"a": 0, "b": 0}
    assert references(parse_formula("next(a)")) == {"a": FULL}
    assert references(parse_formula("cumsum(a)")) == {"a": 0}
    # a name read both directly and through a reduction needs the full series
    assert references(parse_formula("a / total(a)")) == {"a": FULL}
    # the smallest lag wins
    assert references(parse_formula("a + prev(a)")) == {"a": 0}


def test_python_conveniences_lower_to_builtins():
    assert references(parse_formula("x if flag else y")) == {"flag": 0, "x": 0, "y": 0}
    assert references(parse_formula("0 < a <= b and not c")) == {"a": 0, "b": 0, "c": 0}


def test_irr():
    assert irr([-100, 110]) == pytest.approx(0.10)
    assert irr([-100, 0, 0, 0, 0, 200]) == pytest.approx(2 ** (1 / 5) - 1)
    assert irr([100, 100]) != irr([100, 100])  # NaN: no sign change
