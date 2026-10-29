"""Built-in formula functions.

Each built-in knows how to bind its arguments, how references inside each argument relate
in time (``offset``), what shape it returns, and how to evaluate itself on scalars and
pandas Series.

Temporal offsets drive dependency ordering. An offset of ``k >= 0`` means the value at
period ``t`` reads its argument at ``t - k``; :data:`FULL` means it may read any period
(reductions, ``next``), so the argument must be complete before evaluation.
"""

from __future__ import annotations

import functools
import keyword
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..errors import FormulaError
from .env import AxisInfo, FormulaEnv
from .ir import Call, Node, Num, Str, Unary
from .shapes import SCALAR, UNKNOWN, Shape, describe, join_all

FULL = None
REQUIRED = object()


@dataclass(frozen=True)
class Param:
    name: str
    kind: str = "expr"  # "expr" | "int" | "literal" | "axis"
    default: Any = REQUIRED
    temporal: str = "point"  # "point" | "lag" (shifted by the ``lag`` literal) | "full"
    scalar_only: bool = False
    min_value: int | None = None


def _is_series(v: Any) -> bool:
    return isinstance(v, pd.Series)


def _index_of(*values: Any) -> pd.Index | None:
    for v in values:
        if _is_series(v):
            return v.index
    return None


def _as_array(v: Any, index: pd.Index) -> np.ndarray:
    if _is_series(v):
        return v.to_numpy(dtype=float, copy=True)
    return np.full(len(index), float(v))


def _literal_value(node: Node) -> Any:
    if isinstance(node, Str):
        return node.value
    if isinstance(node, Num):
        v = node.value
        return int(v) if float(v).is_integer() else float(v)
    if isinstance(node, Unary) and node.op == "neg" and isinstance(node.operand, Num):
        return -_literal_value(node.operand)
    return REQUIRED


class Builtin:
    name: str = ""
    params: tuple[Param, ...] = ()
    varargs: bool = False
    min_varargs: int = 1
    signature: str = ""
    doc: str = ""

    # -- binding -------------------------------------------------------------------------

    def _param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)

    def bind(self, pos: list[Node], kw: dict[str, Node], col: int | None) -> Call:
        if self.varargs:
            if kw:
                raise FormulaError(
                    "invalid_argument", f"{self.name}() takes no keyword arguments", col=col
                )
            if len(pos) < self.min_varargs:
                raise FormulaError(
                    "invalid_argument",
                    f"{self.name}() needs at least {self.min_varargs} argument(s)",
                    col=col,
                )
            return Call(self.name, varargs=list(pos), col=col)

        if len(pos) > len(self.params):
            raise FormulaError(
                "invalid_argument",
                f"{self.name}() takes at most {len(self.params)} positional argument(s): "
                f"{self.signature}",
                col=col,
            )
        bound: dict[str, Node] = {p.name: n for p, n in zip(self.params, pos, strict=False)}
        for k, node in kw.items():
            if self._param(k) is None:
                valid = ", ".join(p.name for p in self.params)
                raise FormulaError(
                    "invalid_argument",
                    f"{self.name}() has no parameter '{k}' (parameters: {valid})",
                    col=col,
                )
            if k in bound:
                raise FormulaError(
                    "invalid_argument", f"{self.name}() got '{k}' more than once", col=col
                )
            bound[k] = node

        call = Call(self.name, col=col)
        for p in self.params:
            if p.name not in bound:
                if p.default is REQUIRED:
                    raise FormulaError(
                        "invalid_argument",
                        f"{self.name}() is missing argument '{p.name}': {self.signature}",
                        col=col,
                    )
                if p.kind == "expr":
                    if p.default is not None:
                        call.args[p.name] = Num(float(p.default), col=col)
                else:
                    call.literals[p.name] = p.default
                continue
            node = bound[p.name]
            if p.kind == "expr":
                call.args[p.name] = node
                continue
            value = _literal_value(node)
            if p.kind == "axis":
                if not isinstance(value, str):
                    raise FormulaError(
                        "invalid_argument",
                        f"{self.name}() '{p.name}' must be an axis name in quotes",
                        col=col,
                    )
            elif p.kind == "int":
                if not isinstance(value, int) or (p.min_value is not None and value < p.min_value):
                    lower = f" >= {p.min_value}" if p.min_value is not None else ""
                    raise FormulaError(
                        "invalid_argument",
                        f"{self.name}() '{p.name}' must be an integer literal{lower}",
                        col=col,
                    )
            elif value is REQUIRED:
                raise FormulaError(
                    "invalid_argument",
                    f"{self.name}() '{p.name}' must be a literal number or string",
                    col=col,
                )
            call.literals[p.name] = value
        return call

    # -- static analysis -----------------------------------------------------------------

    def offset(self, call: Call, key: str | int) -> int | None:
        if self.varargs:
            return 0
        p = self._param(key)  # type: ignore[arg-type]
        if p is None or p.temporal == "point":
            return 0
        if p.temporal == "lag":
            return call.literals.get("lag", 1)
        return FULL

    def shape(
        self, call: Call, args: dict[str, Shape], varargs: list[Shape], env: FormulaEnv
    ) -> Shape:
        for p in self.params:
            s = args.get(p.name, UNKNOWN)
            if p.scalar_only and s not in (UNKNOWN, SCALAR):
                raise FormulaError(
                    "axis_mismatch",
                    f"{self.name}() '{p.name}' must be a scalar, got a {describe(s)}",
                    col=call.col,
                )
        return join_all([*args.values(), *varargs], call.col)

    # -- evaluation ----------------------------------------------------------------------

    def evaluate(self, call: Call, args: dict[str, Any], varargs: list[Any], env: FormulaEnv):
        raise NotImplementedError


# ---------------------------------------------------------------------------------------
# Element-wise functions


class Where(Builtin):
    name = "where"
    params = (Param("cond"), Param("if_true"), Param("if_false"))
    signature = "where(cond, if_true, if_false)"
    doc = "Element-wise choice. Missing values in cond give missing results."

    def evaluate(self, call, args, varargs, env):
        cond, t, f = args["cond"], args["if_true"], args["if_false"]
        index = _index_of(cond, t, f)
        if index is None:
            c = float(cond)
            if np.isnan(c):
                return np.float64(np.nan)
            return t if c else f
        c = _as_array(cond, index)
        out = np.where(c != 0, _as_array(t, index), _as_array(f, index))
        out[np.isnan(c)] = np.nan
        return pd.Series(out, index=index)


class Abs(Builtin):
    name = "abs"
    params = (Param("x"),)
    signature = "abs(x)"
    doc = "Absolute value."

    def evaluate(self, call, args, varargs, env):
        return np.abs(args["x"])


class Round(Builtin):
    name = "round"
    params = (Param("x"), Param("ndigits", kind="int", default=0))
    signature = "round(x, ndigits=0)"
    doc = "Round to ndigits decimal places."

    def evaluate(self, call, args, varargs, env):
        return np.round(args["x"], call.literals["ndigits"])


class Clip(Builtin):
    name = "clip"
    params = (Param("x"), Param("lower", default=None), Param("upper", default=None))
    signature = "clip(x, lower=None, upper=None)"
    doc = "Limit x to [lower, upper]. Either bound may be omitted."

    def evaluate(self, call, args, varargs, env):
        out = args["x"]
        if "lower" in args:
            out = np.maximum(out, args["lower"])
        if "upper" in args:
            out = np.minimum(out, args["upper"])
        return out


class MinMax(Builtin):
    varargs = True

    def __init__(self, name: str, pairwise, reduce: str):
        self.name = name
        self._pairwise = pairwise
        self._reduce = reduce
        self.signature = f"{name}(a, b, ...) or {name}(series)"
        self.doc = (
            f"With two or more arguments, the element-wise {name}imum. With one series, "
            f"the {name}imum over its axis."
        )

    def offset(self, call, key):
        return FULL if len(call.varargs) == 1 else 0

    def shape(self, call, args, varargs, env):
        if len(varargs) == 1:
            return SCALAR
        return join_all(varargs, call.col)

    def evaluate(self, call, args, varargs, env):
        if len(varargs) == 1:
            x = varargs[0]
            return np.float64(getattr(x, self._reduce)(skipna=False)) if _is_series(x) else x
        return functools.reduce(self._pairwise, varargs)


class Pmt(Builtin):
    name = "pmt"
    params = (
        Param("rate"),
        Param("nper"),
        Param("pv"),
        Param("fv", default=0),
        Param("when", default=0),
    )
    signature = "pmt(rate, nper, pv, fv=0, when=0)"
    doc = "Periodic payment of an annuity (Excel sign convention; when=1 for start of period)."

    def evaluate(self, call, args, varargs, env):
        values = [args[k] for k in ("rate", "nper", "pv", "fv", "when")]
        index = _index_of(*values)
        if index is None:
            rate, nper, pv, fv, when = values
            if rate == 0:
                return np.float64(-(pv + fv) / nper)
            growth = (1 + rate) ** nper
            return np.float64(-(rate * (pv * growth + fv)) / ((1 + rate * when) * (growth - 1)))
        rate, nper, pv, fv, when = (_as_array(v, index) for v in values)
        growth = (1 + rate) ** nper
        with np.errstate(all="ignore"):
            annuity = -(rate * (pv * growth + fv)) / ((1 + rate * when) * (growth - 1))
            zero_rate = -(pv + fv) / nper
        return pd.Series(np.where(rate == 0, zero_rate, annuity), index=index)


# ---------------------------------------------------------------------------------------
# Time shifts and cumulative functions


class Prev(Builtin):
    name = "prev"
    params = (
        Param("x", temporal="lag"),
        Param("lag", kind="int", default=1, min_value=0),
        Param("init", default=None),
    )
    signature = "prev(x, lag=1, init=None)"
    doc = (
        "Value of x `lag` periods earlier. The first `lag` periods take `init` "
        "(missing if omitted)."
    )

    def evaluate(self, call, args, varargs, env):
        x, lag = args["x"], call.literals["lag"]
        if not _is_series(x) or lag == 0:
            return x
        values = x.to_numpy(dtype=float)
        n = len(values)
        out = np.full(n, np.nan)
        if lag < n:
            out[lag:] = values[: n - lag]
        if "init" in args:
            k = min(lag, n)
            out[:k] = _as_array(args["init"], x.index)[:k]
        return pd.Series(out, index=x.index)


class Next(Builtin):
    name = "next"
    params = (
        Param("x", temporal="full"),
        Param("lag", kind="int", default=1, min_value=0),
        Param("fill", default=None),
    )
    signature = "next(x, lag=1, fill=None)"
    doc = "Value of x `lag` periods later. The last `lag` periods take `fill`."

    def evaluate(self, call, args, varargs, env):
        x, lag = args["x"], call.literals["lag"]
        if not _is_series(x) or lag == 0:
            return x
        values = x.to_numpy(dtype=float)
        n = len(values)
        out = np.full(n, np.nan)
        if lag < n:
            out[: n - lag] = values[lag:]
        if "fill" in args:
            k = min(lag, n)
            out[n - k :] = _as_array(args["fill"], x.index)[n - k :]
        return pd.Series(out, index=x.index)


class Cumulative(Builtin):
    params = (Param("x"),)

    def __init__(self, name: str, doc: str):
        self.name = name
        self.signature = f"{name}(x)"
        self.doc = doc

    def evaluate(self, call, args, varargs, env):
        x = args["x"]
        if not _is_series(x):
            return x
        return getattr(x.astype(float), self.name)()


# ---------------------------------------------------------------------------------------
# Reductions to a scalar


class Reduce(Builtin):
    params = (Param("x", temporal="full"),)

    def __init__(self, name: str, fn, doc: str):
        self.name = name
        self._fn = fn
        self.signature = f"{name}(x)"
        self.doc = doc

    def shape(self, call, args, varargs, env):
        return SCALAR

    def evaluate(self, call, args, varargs, env):
        x = args["x"]
        return np.float64(self._fn(x.astype(float))) if _is_series(x) else x


class At(Builtin):
    name = "at"
    params = (Param("x", temporal="full"), Param("period", kind="literal"))
    signature = "at(x, period)"
    doc = "Value of x at a given period, e.g. at(revenue, 2028)."

    def shape(self, call, args, varargs, env):
        s = args.get("x", UNKNOWN)
        if isinstance(s, str) and s in env.axes:
            try:
                env.axes[s].lookup(call.literals["period"])
            except KeyError:
                raise FormulaError(
                    "invalid_argument",
                    f"at(): {call.literals['period']!r} is not on axis '{s}'",
                    col=call.col,
                ) from None
        return SCALAR

    def evaluate(self, call, args, varargs, env):
        x = args["x"]
        if not _is_series(x):
            return x
        target = call.literals["period"]
        for label in x.index:
            if label == target or str(label) == str(target):
                return x.loc[label]
        raise KeyError(target)


class Npv(Builtin):
    name = "npv"
    params = (Param("rate", scalar_only=True), Param("cashflows", temporal="full"))
    signature = "npv(rate, cashflows)"
    doc = "Net present value; the first cash flow is at t=0 and is not discounted."

    def shape(self, call, args, varargs, env):
        super().shape(call, args, varargs, env)
        return SCALAR

    def evaluate(self, call, args, varargs, env):
        cf = args["cashflows"]
        if not _is_series(cf):
            return cf
        values = cf.to_numpy(dtype=float)
        t = np.arange(len(values))
        return np.float64(np.sum(values / (1.0 + args["rate"]) ** t))


def irr(cashflows) -> float:
    values = np.asarray(cashflows, dtype=float)
    if values.size < 2 or np.isnan(values).any():
        return np.nan
    if not (np.any(values > 0) and np.any(values < 0)):
        return np.nan
    t = np.arange(values.size)

    def npv(r):
        return np.sum(values / (1.0 + r) ** t, axis=-1)

    grid = np.concatenate([np.linspace(-0.99, 1.0, 400), np.geomspace(1.0, 1000.0, 200)[1:]])
    with np.errstate(all="ignore"):
        vals = npv(grid[:, None])
    signs = np.sign(vals)
    brackets = np.nonzero(signs[:-1] * signs[1:] < 0)[0]
    if brackets.size == 0:
        return np.nan
    i = brackets[np.argmin(np.abs(grid[brackets] - 0.1))]
    lo, hi = grid[i], grid[i + 1]
    f_lo = vals[i]
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = npv(mid)
        if f_mid == 0 or hi - lo < 1e-14:
            break
        if np.sign(f_mid) == np.sign(f_lo):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return float(0.5 * (lo + hi))


class Irr(Builtin):
    name = "irr"
    params = (Param("cashflows", temporal="full"),)
    signature = "irr(cashflows)"
    doc = "Internal rate of return of a cash-flow series (missing if it has no sign change)."

    def shape(self, call, args, varargs, env):
        return SCALAR

    def evaluate(self, call, args, varargs, env):
        cf = args["cashflows"]
        return np.float64(irr(cf.to_numpy(dtype=float))) if _is_series(cf) else np.float64(np.nan)


# ---------------------------------------------------------------------------------------
# Axis helpers


class AxisHelper(Builtin):
    params = (Param("axis", kind="axis", default=None),)

    def __init__(self, name: str, fn, doc: str):
        self.name = name
        self._fn = fn
        self.signature = f"{name}(axis=None)"
        self.doc = doc

    def shape(self, call, args, varargs, env):
        return env.resolve_axis(call.literals.get("axis"), self.name, call.col).name

    def evaluate(self, call, args, varargs, env):
        ax = env.resolve_axis(call.literals.get("axis"), self.name, call.col)
        return pd.Series(self._fn(ax), index=ax.index)


def _period_values(ax: AxisInfo):
    return ax.index.to_numpy()


BUILTINS: dict[str, Builtin] = {
    b.name: b
    for b in [
        Where(),
        Abs(),
        Round(),
        Clip(),
        MinMax("min", np.minimum, "min"),
        MinMax("max", np.maximum, "max"),
        Pmt(),
        Prev(),
        Next(),
        Cumulative("cumsum", "Running total."),
        Cumulative("cumprod", "Running product."),
        Reduce("total", lambda s: s.sum(skipna=False), "Sum over the axis."),
        Reduce("sum", lambda s: s.sum(skipna=False), "Sum over the axis (alias of total)."),
        Reduce("mean", lambda s: s.mean(skipna=False), "Mean over the axis."),
        Reduce("first", lambda s: s.iloc[0], "First value on the axis."),
        Reduce("last", lambda s: s.iloc[-1], "Last value on the axis."),
        At(),
        Npv(),
        Irr(),
        AxisHelper(
            "is_forecast", lambda ax: ~ax.historical, "True in forecast (non-historical) periods."
        ),
        AxisHelper("is_historical", lambda ax: ax.historical.copy(), "True in historical periods."),
        AxisHelper("period", _period_values, "The axis labels, e.g. the fiscal year."),
        AxisHelper(
            "period_index", lambda ax: np.arange(len(ax), dtype=float), "0, 1, 2, ... per period."
        ),
    ]
}

RESERVED_NAMES: frozenset[str] = frozenset(
    {*BUILTINS, *keyword.kwlist, *keyword.softkwlist, "True", "False", "None"}
)
