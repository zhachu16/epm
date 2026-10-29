# fm

A financial modelling engine where models are built from **named metrics and formulas**
instead of cell references, evaluated with pandas. See [`docs/PLAN.md`](docs/PLAN.md) for
the full design and milestones.

## Status

Milestones M0–M1 are done: the formula language, model schema, dependency graph and
evaluator, including `prev()` recurrences. The CLI, archives, sources, the solver for declared
circularities, sweeps and the UI are still to come.

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

```python
from fm import Engine, load_model

engine = Engine(load_model("tests/fixtures/three_statement.json"))
result = engine.run()
print(result["net_income"])
print(result.checks["balance_sheet_balances"].passed)

# temporary assumptions and selected outputs; the model itself is unchanged
engine.run(overrides={"interest_rate": 0.09}, outputs=["cumulative_fcf"])

engine.trace("ebit", direction="upstream")
```

## Formulas

Formulas are Python expressions over metric names:

```text
ebitda - depreciation
prev(cash, init=opening_cash) + fcf - repayment
where(is_forecast(), revenue * margin, 0)
x if flag else y
irr(equity_cashflows)
```

| Kind | Functions |
| --- | --- |
| Time | `prev(x, lag=1, init=None)`, `next(x, lag=1, fill=None)`, `cumsum`, `cumprod` |
| Element-wise | `where`, `min(a, b, ...)`, `max(a, b, ...)`, `abs`, `round`, `clip`, `pmt` |
| Reductions | `total`/`sum`, `mean`, `first`, `last`, `min(x)`, `max(x)`, `at(x, period)` |
| Finance | `irr`, `npv` |
| Axis | `is_forecast()`, `is_historical()`, `period()`, `period_index()` |

Scalars broadcast across a metric's axis, and axes are inferred from formulas when they
aren't declared. A loop that passes through `prev()` is evaluated one period at a time. A
loop within the same period is rejected, and the error shows the path around it.
