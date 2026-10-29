# epm

A financial modelling engine where models are built from **named metrics and formulas**
instead of cell references, evaluated with pandas. Each quantity is defined once. Tables,
analyses and checks refer to it by name. Agents and people edit models through the same
structured operations, from the web UI, the `fm` CLI or the Python API.

See [`docs/PLAN.md`](docs/PLAN.md) for the full design and milestones.

## Status

All milestones (M0–M12) are done: the engine, sources, solver, archives and cache, editing
operations, custom functions, sweeps and analyses, the `fm` CLI, a complete sample LBO, the
`fm serve` session server, the web UI, and a performance pass. A 1,000-scenario sweep of the
sample LBO takes a few seconds; `python benchmarks/run.py` measures it.

**Agents** building or changing models should start with the
[agent guide](docs/agent-guide.md). People working on epm itself: see
[`AGENTS.md`](AGENTS.md).

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

The sample LBO lives in [`examples/lbo`](examples/lbo). It's an unpacked model directory;
`fm pack` turns it into a `.epm` archive.

```bash
fm -m examples/lbo validate
```

```bash
fm -m examples/lbo table show returns
```

```bash
fm -m examples/lbo run --set interest_rate=0.08 --output equity_irr
```

```bash
fm -m examples/lbo sweep --input "interest_rate=0.04,0.06,0.08" --output moic_3y
```

```bash
fm -m examples/lbo analysis run entry_exit_irr --plot irr.png
```

```bash
fm -m examples/lbo trace ebit --upstream
```

Add `--json` to any command for a `{ok, data, errors, warnings}` envelope.

## Web UI

The local UI lives in [`frontend-local/`](frontend-local), a standalone Vite + React app. Build it once:

```bash
cd frontend-local && npm ci && npm run build
```

Then serve a model and open the printed URL:

```bash
fm -m examples/lbo serve --open
```

`fm serve` holds the session: undo and redo history, and proposals waiting for review.
While it runs, `fm` commands for that model run inside the session, so edits from the CLI,
agents and the browser share one history and appear live in the UI. Agents propose changes
with `fm apply changes.json --propose`; they show up in the **Changes** panel with a diff
and their effect on key outputs, ready to commit or reject.

## Building a model

```bash
fm init deal.epm --years 2024-2030 --historical 2024
```

```bash
fm metric set growth --value 0.05 --unit %
```

```bash
fm metric set revenue --formula "prev(revenue, init=100) * (1 + growth)" --unit "USD m"
```

```bash
fm metric rename growth revenue_growth
```

Commands also exist for `axis`, `source`, `file`, `function`, `table`, `check`,
`circularity`, `analysis` and `settings`. Renames update every reference, and deletes fail
while anything still uses the object. `fm apply changes.json` applies a batch of operations
atomically; `--dry-run` previews the definition diff and the effect on key outputs:

```json
{"ops": [
  {"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}},
  {"op": "create", "kind": "metric", "name": "fees", "def": {"formula": "0.02 * enterprise_value"}},
  {"op": "rename", "kind": "metric", "name": "fees", "to": "advisory_fees"}
]}
```

The same API from Python:

```python
from epm import Workspace

ws = Workspace.open("examples/lbo")
ws.run(overrides={"interest_rate": 0.06}, outputs=["equity_irr"])
ws.sweep({"entry_multiple": [8, 9, 10], "exit_multiple": [8, 9, 10]}, ["equity_irr"])
edit = [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}]
preview = ws.apply(edit, mode="dry_run")   # diff + effect on key outputs, nothing changes
ws.apply(edit)                             # commit
ws.undo()                                  # undo history lives in the session
ws.save("atlas.epm")
```

## Formulas

Formulas are Python expressions over metric names:

```text
ebitda - depreciation_amortization
prev(cash, init=minimum_cash) + fcf - repayment
where(is_forecast(), revenue * margin, 0)
x if flag else y
irr(equity_cash_flows)
amortization_schedule(tlb_amount, tlb_amortization_pct, is_forecast())   # custom function
```

| Kind | Functions |
| --- | --- |
| Time | `prev(x, lag=1, init=None)`, `next(x, lag=1, fill=None)`, `cumsum`, `cumprod` |
| Element-wise | `where`, `min(a, b, ...)`, `max(a, b, ...)`, `abs`, `round`, `clip`, `pmt` |
| Reductions | `total`/`sum`, `mean`, `first`, `last`, `min(x)`, `max(x)`, `at(x, period)` |
| Finance | `irr`, `npv` |
| Axis | `is_forecast()`, `is_historical()`, `period()`, `period_index()` |

`fm list builtins` prints every function with its signature and a description.

- **Axes.** Scalars broadcast across a metric's axis, and axes are inferred from formulas
  when they aren't declared.
- **Recurrences.** A loop that passes through `prev()` is evaluated one period at a time.
- **Circular references.** A same-period loop is rejected, and the error lists the loop's
  path and every metric in it. To solve it iteratively instead, declare those metrics under
  `circularities`. Interest on average debt balances in the LBO works this way.
- **Forecast-only metrics.** `"scope": "forecast"` marks metrics that only exist after the
  historical periods, such as post-deal debt. For these, `prev(x, init=...)` starts at the
  first forecast year, and reductions like `irr` skip history.
- **Units.** Undeclared units are inferred. Adding, subtracting or comparing different units
  warns, or gives an error with `settings.strict_units`.
- **Selective recalculation.** Every evaluation step has a fingerprint covering its
  definitions, input files and upstream fingerprints. After an edit, or in a sweep, only
  what changed is recomputed.

## Sources and history

```json
{
  "sources": {
    "is":  { "file": "inputs/historicals.xlsx", "sheet": "IS", "range": "A1:H40" },
    "ops": { "file": "inputs/ops.csv", "orientation": "columns" }
  },
  "metrics": {
    "revenue": {
      "unit": "USD m",
      "history": { "source": "is", "item": "Revenue" },
      "formula": "prev(revenue) * (1 + revenue_growth)"
    },
    "volume":         { "input": { "source": "ops", "item": "volume" } },
    "entry_multiple": { "unit": "x", "input": { "source": "is", "cell": "Deal!B4" } }
  }
}
```

`history` takes the axis's historical periods from the source, and the formula fills the
forecast. So `prev(revenue)` in the first forecast year reads the last actual.

## Custom functions

`functions.py` holds functions decorated with `@function`. Formulas call them with
explicit arguments:

```python
from epm import function
import numpy as np

@function                      # returns="series" (default) or "scalar"
def amortization_schedule(principal, annual_pct, is_forecast):
    return np.where(np.asarray(is_forecast) > 0, principal * annual_pct, 0.0)
```

- **Discovery.** The engine reads `functions.py` with Python's parser and never runs it in
  the main process.
- **Execution.** Calls run in a separate worker process (`python -I`). The worker has
  restricted builtins, an import allowlist (numpy, pandas, math, …), memory and CPU-time
  limits, and a wall-clock timeout.
- **Not a security boundary.** This contains runaway or careless code but cannot stop
  malicious code, so only open models whose functions you trust.

## Packages

A `.epm` file is an ordinary ZIP archive:
- `model.json` holds the definitions;
- `inputs/` holds the source files;
- `functions.py` is optional;
- `cache/` is optional and holds calculated values, keyed by fingerprint as parquet files.

Archives are written atomically and deterministically. The same layout works unpacked in a
directory; `fm pack` and `fm unpack` convert between the two.
