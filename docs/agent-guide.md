# Agent guide

This guide is for AI agents that build, inspect and change epm models. Everything here works
through the `fm` CLI with `--json`; the same operations exist in the Python API (see the end).
On the hosted service the same batches, definitions and formulas apply through MCP tools such
as `apply_batch` and `show`, with a `project_id`; see the [user guide](user-guide.md#mcp-reference).

## The model in one minute

- A **metric** is a named quantity defined once: a fixed value, values per period, a value
  read from a source file, or a **formula** over other metrics' names, such as
  `ebitda - depreciation_amortization`. Formulas never use cell references.
- A metric is either a **scalar** (`tax_rate`) or a **series** over an **axis**, usually
  fiscal years (`fy`). Scalars broadcast across a series' periods. Axes are inferred from
  formulas when they aren't declared.
- An axis marks some periods as **historical**. `history` fills them from a source, and the
  formula fills the forecast, so `prev(revenue)` in the first forecast year reads the last
  actual. Metrics with `"scope": "forecast"` only exist after the historical periods.
- **Tables** order metrics for display, **checks** are rules that must hold (a balance sheet
  that balances), **analyses** are saved sensitivities, **circularities** declare
  same-period loops that are solved by iteration, and **functions** are custom Python in
  `functions.py`, run in an isolated worker.

A model lives in a `.epm` archive (a ZIP) or an unpacked directory with `model.json`,
`inputs/`, an optional `functions.py`, and a `cache/` of calculated values.

## Ground rules

1. **Always pass `--json`** and read the envelope: `{"ok", "data", "errors", "warnings"}`.
   The exit code is 1 when `ok` is false, 2 for a malformed command. Every error has a
   `code` and a `message`, and many carry `suggestions` for misspelled names.
2. **Read before you write.** Use `fm list`, `fm show`, `fm trace` and `fm table show` to
   learn the names, units and structure. Never guess a metric name.
3. **Change the model with operations, never by editing `model.json`.** Put related changes
   in one batch and apply it with `fm apply`. A batch is atomic: if any operation fails, or
   the result has new validation errors, nothing changes.
4. **Preview first.** `fm apply changes.json --dry-run` shows the definition diff and the
   effect on the key outputs, without changing anything. Read both before committing.
5. **When a person has the model open, propose.** If `fm serve` is running for the model,
   every `fm` command runs inside that session. `fm apply changes.json --propose` queues the
   batch in the person's **Changes** panel with its diff and effect; they commit or reject
   it. Commit directly only when you were asked to.
6. **Name every assumption.** Don't bury numbers in formulas: `revenue * 0.19` hides an
   assumption, `revenue * sga_pct_revenue` exposes it to sensitivities. Give metrics a
   `unit`, a `folder` and, for anything non-obvious, a `description`.
7. **Fix the root cause of a diagnostic.** Errors name the object (`metric:sga`) and the
   problem; they are listed under [diagnostics](#diagnostics). Don't silence a check by
   loosening its tolerance unless that is the actual fix.

## Inspecting a model

```bash
fm -m examples/lbo --json list
```

```bash
fm -m examples/lbo --json list inputs --folder Assumptions/Operations
```

```bash
fm -m examples/lbo --json show interest_expense
```

```bash
fm -m examples/lbo --json trace equity_irr --upstream --depth 2
```

```bash
fm -m examples/lbo --json table show income_statement
```

```bash
fm -m examples/lbo --json validate
```

- **`list [kind]`** lists metrics (default), `inputs`, `axes`, `sources`, `functions`,
  `tables`, `checks`, `circularities`, `analyses`, `files`, or `builtins` (the formula
  functions, with signatures). `--search` matches names, folders and descriptions.
- **`show NAME`** gives the definition and, for a metric, its value, axis, unit, status,
  dependencies (`depends_on`) and dependants (`used_by`).
- **`trace NAME`** walks dependencies `--upstream` (what it reads) or `--downstream` (what
  reads it).
- **`validate`** reports every diagnostic without running scenarios.

Set `EPM_MODEL` instead of repeating `-m`.

## Running scenarios

`run`, `sweep` and saved analyses never change the model.

```bash
fm -m examples/lbo --json run --set interest_rate=0.06 --set exit_multiple=8 --output equity_irr,moic
```

```bash
fm -m examples/lbo --json sweep --input interest_rate=0.03:0.08:0.01 --input tlb_leverage=3,4,5 --output equity_irr --output tlb_close@2027
```

```bash
fm -m examples/lbo --json analysis run entry_exit_irr
```

- **Outputs.** A scalar is named directly. A series needs a period: `tlb_close@2027`.
- **Inputs.** `NAME=v1,v2,...` lists values; `NAME=start:stop:step` is an inclusive range.
  `--mode grid` (the default) runs every combination; `--mode zip` pairs values by position.
- **Failures.** A scenario that fails is reported in its row with `status` and `error`; the
  rest still run.
- **Speed.** Scenarios reuse everything the swept inputs don't affect. A 1,000-scenario
  sweep of the sample LBO takes a few seconds.

## Changing a model

### Batches

A batch is a JSON object with a `label` and a list of `ops`:

```json
{
  "label": "Add a management fee",
  "ops": [
    {"op": "create", "kind": "metric", "name": "management_fee_rate",
     "def": {"value": 0.01, "unit": "%", "folder": "Assumptions/Operations"}},
    {"op": "create", "kind": "metric", "name": "management_fee",
     "def": {"formula": "revenue * management_fee_rate * is_forecast()", "unit": "USD m",
             "folder": "Income statement"}},
    {"op": "update", "kind": "metric", "name": "sga",
     "set": {"formula": "revenue * sga_pct_revenue + management_fee"}}
  ]
}
```

```bash
fm -m examples/lbo --json apply changes.json --dry-run
```

`fm apply -` reads the batch from stdin. The result (`data`) contains:

- **`ok`** and **`committed`**: whether the batch is valid, and whether it was saved.
- **`errors`**: for each failure, the `index` of the operation (or `-1` for validation of
  the result), its `code` and `message`.
- **`changes`**: every object `added`, `changed`, `removed` or `renamed`, with its
  definition `before` and `after`.
- **`impact`**: for each key output (`settings.key_outputs`) and each changed metric, the
  value `before` and `after`, with a `delta` for scalars; and every check that changes
  state or does not pass afterwards.
- **`new_errors`**: diagnostics the batch would introduce.

The batch above, previewed on the sample LBO, lowers `equity_irr` from 17.3% to 15.3%. Had
it referred to `sga_pct` instead of `sga_pct_revenue`, the preview would have failed with
`unknown_reference: unknown metric 'sga_pct'; did you mean sga_pct_revenue, sga?`.

### Operations

| `op` | Fields | Meaning |
| --- | --- | --- |
| `create` | `kind`, `name`, `def` | Add an object. Fails if the name is taken. |
| `update` | `kind`, `name`, `set` | Merge `set` into the definition; `null` clears a field. |
| `upsert` | `kind`, `name`, `set` | `update` if it exists, otherwise `create` from `set`. |
| `delete` | `kind`, `name` | Remove it. Fails while anything still references it. |
| `rename` | `kind`, `name`, `to` | Rename it and update every reference, in formulas, tables, checks, analyses and circularities. |

`kind` is one of `metric`, `axis`, `source`, `table`, `check`, `circularity`, `analysis`,
`function`, `functions`, `file` or `settings`. `settings` and `functions` (the whole of
`functions.py`) take `update` with `set` and no name. `file` operations carry the file's
bytes as base64 in `data`, under a name such as `inputs/budget.csv`.

Operations apply in order, so a batch can create a metric and then use it.

### Definitions

**metric**: exactly one of `value`, `input` or `formula`, plus optional fields.

| Field | Example | Notes |
| --- | --- | --- |
| `value` | `0.25`, `{"2025": 0.08, "2026": 0.07}` | A scalar, or values per period. |
| `input` | `{"source": "deal", "cell": "B2"}`, `{"source": "ops", "item": "volume"}` | A cell, or a series (`item` is the row or column label). `scale` multiplies, e.g. `0.001`. |
| `formula` | `"prev(cash, init=minimum_cash) + fcf"` | See [formulas](#formulas). |
| `history` | `{"source": "is", "item": "Revenue"}` | Historical periods of a formula metric. |
| `axis` | `"fy"` | Usually inferred. |
| `unit` | `"USD m"`, `"%"`, `"x"` | Inferred when omitted. |
| `scope` | `"forecast"` | Only exists after the historical periods. Usually inferred. |
| `folder`, `description`, `tags` | `"Assumptions/Financing"` | Organisation and documentation. |

**axis**: `{"values": [2024, 2025, 2026], "historical": [2024], "kind": "period"}`.

**source**: `{"file": "inputs/historicals.xlsx", "sheet": "IS", "range": "A1:H40",
"orientation": "rows", "axis": "fy"}`. With `rows`, each row is a series and the first row
holds the axis labels; with `columns`, each column is a series. `index` and `labels` pick a
different row or column for the labels and names. CSV files take a `delimiter`.

**table**: `{"rows": [{"header": "Income statement"}, {"metric": "revenue"},
{"metric": "ebitda", "style": "subtotal", "label": "EBITDA"}]}`.

**check**: `{"expr": "total_assets - total_liabilities_and_equity", "tolerance": 1e-6,
"severity": "error"}`. It passes where the expression is true, or within `tolerance` of
zero. `severity` is `error` or `warning`.

**circularity**: `{"metrics": ["interest_expense", "net_income", "cash"], "tol": 1e-9,
"max_iter": 100, "damping": 1.0, "seed": 0}`.

**analysis**: `{"inputs": [{"metric": "tax_rate", "values": [0.2, 0.25, 0.3]}],
"outputs": ["equity_irr"], "mode": "grid", "overrides": {"exit_multiple": 8},
"plot": {"kind": "line", "x": "tax_rate", "y": ["equity_irr"], "format": ".1%"}}`. An input
can use `"range": {"start": 0.02, "stop": 0.07, "step": 0.01}` instead of `values`; a
heatmap takes `x` and `y` (both inputs) and `z` (an output).

**function**: `{"code": "<one @function definition>"}`. **functions**: `{"code": "<the
whole of functions.py>"}`. **settings**: `name`, `currency`, `default_axis`,
`strict_units` and `key_outputs` (the metrics every preview reports).

### Recipes

A sensitivity to keep, and a check:

```json
{
  "label": "Tax sensitivity and a leverage warning",
  "ops": [
    {"op": "create", "kind": "analysis", "name": "tax_sensitivity",
     "def": {"inputs": [{"metric": "tax_rate", "range": {"start": 0.2, "stop": 0.3, "step": 0.025}}],
             "outputs": ["equity_irr", "moic"],
             "plot": {"kind": "line", "x": "tax_rate", "y": ["equity_irr"], "format": ".1%"}}},
    {"op": "create", "kind": "check", "name": "leverage_below_seven",
     "def": {"expr": "leverage <= 7", "severity": "warning",
             "description": "Total debt stays below 7x EBITDA"}}
  ]
}
```

A clearer name, with every reference updated:

```json
{
  "label": "Spell out working capital days",
  "ops": [
    {"op": "rename", "kind": "metric", "name": "dso", "to": "receivable_days"},
    {"op": "rename", "kind": "metric", "name": "dpo", "to": "payable_days"},
    {"op": "update", "kind": "metric", "name": "receivable_days",
     "set": {"unit": "days", "description": "Receivables as days of revenue"}}
  ]
}
```

A custom function. Custom functions receive pandas Series or floats and return a series on
the same axis (or a scalar with `returns="scalar"`):

```json
{
  "label": "Straight-line amortization",
  "ops": [
    {"op": "create", "kind": "function", "name": "straight_line",
     "def": {"code": "@function\ndef straight_line(amount, years, is_forecast):\n    \"\"\"Equal repayments of amount over years, in forecast periods.\"\"\"\n    return np.where(np.asarray(is_forecast) > 0, amount / years, 0.0)\n"}},
    {"op": "create", "kind": "metric", "name": "notes_amortization",
     "def": {"formula": "straight_line(notes_amount, 7, is_forecast())", "unit": "USD m",
             "folder": "Debt"}}
  ]
}
```

Use built-in formulas when they can express the logic; custom functions are for logic they
can't, such as schedules from external tables. Functions may import only numpy, pandas and
a set of standard-library modules (`math`, `statistics`, `itertools`, …), and run with time
and memory limits.

## Formulas

Formulas are Python expressions: arithmetic (`+ - * / // % **`), comparisons, `and`, `or`,
`not`, `x if cond else y`, numbers, metric names and the functions below. `fm list builtins`
prints the same list from the code.

| Function | Meaning |
| --- | --- |
| `prev(x, lag=1, init=None)` | `x` from `lag` periods earlier; the first periods take `init`. For a forecast-only `x`, the first forecast periods take `init`. |
| `next(x, lag=1, fill=None)` | `x` from `lag` periods later; the last periods take `fill`. |
| `cumsum(x)`, `cumprod(x)` | Running total, running product. |
| `where(cond, if_true, if_false)` | Element-wise choice. |
| `min(a, b, ...)`, `max(a, b, ...)` | Element-wise minimum and maximum. With one series, the minimum or maximum over the axis. |
| `abs(x)`, `round(x, ndigits=0)`, `clip(x, lower=None, upper=None)` | Element-wise. |
| `pmt(rate, nper, pv, fv=0, when=0)` | Annuity payment, Excel's sign convention. |
| `total(x)`, `sum(x)`, `mean(x)`, `first(x)`, `last(x)` | Reductions to a scalar. |
| `at(x, period)` | The value at one period, e.g. `at(ebitda, 2024)`. |
| `irr(cashflows)`, `npv(rate, cashflows)` | Internal rate of return; net present value (the first cash flow undiscounted). |
| `is_forecast()`, `is_historical()` | True in forecast or historical periods. |
| `period()`, `period_index()` | The axis labels (e.g. 2027), or 0, 1, 2, … |

- **Time.** `prev` makes a recurrence: `cash = prev(cash, init=minimum_cash) + fcf` is
  evaluated one period at a time. Reductions (`total`, `irr`, …) and `next` read every
  period, so they cannot feed back into a recurrence.
- **Same-period loops.** Interest on average balances depends on the cash sweep, which
  depends on interest. Such a loop is an error (`circular_reference`) until its members are
  declared under a circularity, which solves it by iteration in each period.
- **Missing values.** Out-of-scope and unfilled periods are missing (NaN), and
  missing values propagate. A metric with missing or infinite values gets a warning that
  names the periods.

## Live sessions

`fm serve` holds the model for the web UI. While it runs, a lock file records where:
`.epm-session.lock` inside a model directory, or `.deal.epm.lock` next to `deal.epm`. `fm`
commands for that model are then forwarded to the session, so the person sees your changes
immediately and can undo them.

| Command | Use |
| --- | --- |
| `fm apply changes.json --propose` | Queue a batch for review. The result carries its `id`. |
| `fm pending list`, `fm pending show ID` | Proposals waiting for review. |
| `fm pending commit ID`, `fm pending reject ID` | Act on a proposal, when asked to. |
| `fm history` | Batches committed in this session. |
| `fm undo`, `fm redo`, `fm rollback ID` | Step back through them. |

Without a session these commands fail with `no_session`: history and proposals live in the
session's memory, and each standalone `fm` command is its own session. `fm serve`, `init`,
`pack` and `unpack` always run locally.

## Diagnostics

Each diagnostic has a `severity` (`error`, `warning` or `info`), a `code`, a `message` and
an `object` such as `metric:sga`. Errors block a commit unless `--allow-errors` is passed;
metrics that depend on an erroring metric report `blocked`.

| Code | Meaning | Usual fix |
| --- | --- | --- |
| `unknown_reference` | A formula names a metric that doesn't exist. | Use one of the `suggestions`, or create the metric. |
| `circular_reference` | A same-period loop. The message gives the path and lists every member. | Use `prev()` if the loop should cross periods; otherwise declare the members in a circularity. |
| `not_converged` | A declared loop did not settle. | Check the loop's logic; raise `max_iter`, or set `damping` below 1. |
| `noncausal_recurrence` | A recurrence reads every period of its own members (`total`, `irr`, `next`). | Move the reduction out of the loop. |
| `recurrence_axis` | A recurrence mixes scalars or axes. | Declare an `axis` on the metric, or set `settings.default_axis`. |
| `axis_mismatch` | Series on different axes are combined. | Align the axes, or reduce one side to a scalar. |
| `unit_mismatch` | Adding or comparing different units (an error with `strict_units`). | Convert, or correct a `unit`. |
| `syntax_error`, `disallowed_syntax`, `invalid_argument` | The formula can't be parsed or calls a function wrongly. | Read the message; `fm list builtins` shows signatures. |
| `unknown_function` | A formula calls something that is neither built in nor in `functions.py`. | Add the function, or fix the name. |
| `function_error`, `timeout`, `cpu_limit`, `memory_limit`, `load_error` | A custom function failed, ran too long, or `functions.py` didn't load. | Fix the function; dependants are blocked until then. |
| `calc_error` | Evaluation raised an error. | The message names the period and metric. |
| `missing_value`, `nonfinite_value` | Missing or infinite values (warnings). | Often a missing `init`, `history` or a division by zero. |
| `check_failed` | A check doesn't hold; `failing` lists the periods and values. | Fix the model, not the check. |
| `unknown_source`, `missing_file`, `unreadable_file`, `unmatched_labels` | Source problems. | Check the file path, sheet, range and labels. |
| `unused_circularity`, `unused_history`, `uncached_formula` | Warnings about definitions that have no effect, or Excel cells with no saved value. | Remove the definition, or open and save the file in Excel. |

## Python API

The CLI and the server are thin layers over `Workspace`:

```python
from epm import Workspace

ws = Workspace.open("examples/lbo")
result = ws.run(overrides={"interest_rate": 0.06}, outputs=["equity_irr"])
print(result["equity_irr"], result.ok, result.diagnostics)

sweep = ws.sweep({"tax_rate": [0.2, 0.25, 0.3]}, ["equity_irr", "tlb_close@2027"])
print(sweep.frame())

preview = ws.apply([{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}],
                   mode="dry_run")
print(preview.to_dict()["impact"])
ws.close()
```

`ws.apply(ops, mode="commit")` commits and `ws.undo()` reverts. `ws.save(path)` writes an
archive or directory.
