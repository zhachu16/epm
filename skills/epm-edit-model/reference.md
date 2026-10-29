# epm reference for agents

The vocabulary shared by the epm skills: the MCP tools, operations, definitions, formulas
and diagnostics. The epm connector is the MCP server at
`https://epm-nm1g.onrender.com/mcp`; its tools are listed below.

## Tools

Every tool that works on an existing project takes `project_id`. Errors come back as tool
errors that start with a code, e.g. `read_only_project: ...` or `version_conflict: ...`.

| Tool | Use it to |
| --- | --- |
| `list_projects`, `search_projects` | Find projects and their `project_id` |
| `create_project` | Start an empty model (`name`, `description`), or copy one with `from_project` |
| `get_project` | Read a project's name, description and `current_version` |
| `rename_project`, `duplicate_project`, `delete_project`, `undelete_project` | Manage projects |
| `list_objects` | List `metrics`, `inputs`, `axes`, `sources`, `functions`, `tables`, `checks`, `circularities`, `analyses`, `files` or `builtins`; `search` and `folder` narrow it |
| `show` | One object: definition, value, unit, axis, status, `depends_on`, `used_by` |
| `trace` | Dependencies `upstream` (what it reads) or `downstream` (what reads it) |
| `show_table` | A table's rows with values per period |
| `validate` | Every diagnostic, including failed checks |
| `list_builtins` | Formula functions with signatures |
| `apply_batch` | Change the model: `ops`, `mode` (`dry_run`, the default, or `commit`), `label`, `expected_version` |
| `run` | Evaluate with temporary `overrides`; nothing is saved |
| `sweep` | Scenarios over input values (`grid` or `zip`); series outputs need a period, e.g. `debt_close@2029` |
| `run_analysis` | Run a saved analysis; `save` (and `plot`) keep the results as outputs |
| `list_files`, `list_outputs`, `export_project` | Files, saved outputs and the `.epm` archive, with download URLs |
| `list_versions`, `undo`, `rollback` | History and recovery; both restore an earlier version as a new one |

The example project, `example-lbo`, is read-only: copy it with
`create_project(name=..., from_project="example-lbo")`.

## Operations

`apply_batch` takes a list of operations, applied in order and atomically: if one fails, or
the result has new errors, nothing is saved. `dry_run` returns the same result without
saving: `changes` (each object added, changed, removed or renamed, before and after),
`impact` (key outputs and changed metrics before and after, and checks that change), and
`errors`.

| `op` | Fields | Meaning |
| --- | --- | --- |
| `create` | `kind`, `name`, `def` | Add an object. Fails if the name is taken. |
| `update` | `kind`, `name`, `set` | Merge `set` into the definition; `null` clears a field. |
| `upsert` | `kind`, `name`, `set` | `update` if it exists, otherwise `create` from `set`. |
| `delete` | `kind`, `name` | Remove it. Fails while anything still uses it. |
| `rename` | `kind`, `name`, `to` | Rename it and update every reference. |

`kind` is `metric`, `axis`, `source`, `table`, `check`, `circularity`, `analysis`,
`function`, `functions`, `file` or `settings`. `settings` and `functions` (all of
`functions.py`) take `update` with `set` and no `name`. A `file` carries its bytes as base64
in `data`, under a name such as `inputs/budget.csv`:
`{"op": "upsert", "kind": "file", "name": "inputs/budget.csv", "data": "aXRlbSwyMDI2..."}`.

## Definitions

**metric**: exactly one of `value`, `input` or `formula`, plus optional fields.

| Field | Example | Notes |
| --- | --- | --- |
| `value` | `0.25`, `{"2026": 0.08, "2027": 0.07}` | A scalar, or values per period. |
| `input` | `{"source": "deal", "cell": "B2"}`, `{"source": "ops", "item": "volume"}` | A cell, or a row/column of a source file. `scale` multiplies. |
| `formula` | `"prev(cash, init=minimum_cash) + fcf"` | See formulas below. |
| `history` | `{"source": "is", "item": "Revenue"}` | Historical periods of a formula metric; the formula fills the forecast. |
| `unit` | `"USD m"`, `"%"`, `"x"`, `"days"` | Inferred when omitted; give it anyway. |
| `axis` | `"fy"` | Usually inferred. |
| `scope` | `"forecast"` | Only exists after the historical periods. Usually inferred. |
| `folder`, `description` | `"Assumptions/Financing"` | Organisation and documentation. |

**axis**: `{"values": [2026, 2027, 2028], "historical": [], "label": "Fiscal year"}`. Set
`settings.default_axis` to it.

**source**: `{"file": "inputs/historicals.xlsx", "sheet": "IS", "range": "A1:H40",
"orientation": "rows"}`. With `rows`, each row is a series and the first row holds the
period labels; with `columns`, each column is a series.

**table**: `{"rows": [{"header": "Income statement"}, {"metric": "revenue"},
{"metric": "ebitda", "label": "EBITDA", "style": "subtotal"}]}`. `style` is `normal`,
`subtotal` or `total`.

**check**: `{"expr": "total_assets - total_liabilities_and_equity", "tolerance": 1e-6,
"severity": "error"}`. Passes where the expression is true, or within `tolerance` of zero.

**circularity**: `{"metrics": ["interest_expense", "net_income", "cash"]}`, optionally with
`tol`, `max_iter`, `damping` and `seed`.

**analysis**: `{"inputs": [{"metric": "tax_rate", "values": [0.2, 0.25, 0.3]}],
"outputs": ["equity_irr"], "mode": "grid", "plot": {"kind": "line", "x": "tax_rate",
"y": ["equity_irr"], "format": ".1%"}}`. An input can take `"range": {"start": 0.02,
"stop": 0.07, "step": 0.01}` instead of `values`. A heatmap takes `x` and `y` (inputs) and
`z` (an output).

**function**: `{"code": "@function\ndef name(...):\n    ..."}`: custom Python for logic
formulas can't express. Only numpy, pandas and some standard modules can be imported.

**settings**: `name`, `currency`, `default_axis`, `strict_units`, and `key_outputs` (the
metrics every preview reports).

## Formulas

Python expressions over metric names: `+ - * / // % **`, comparisons, `and`, `or`, `not`,
`x if cond else y`, numbers, metric names and these functions. No cell references.

| Function | Meaning |
| --- | --- |
| `prev(x, lag=1, init=None)` | `x` from `lag` periods earlier; the first periods take `init`. |
| `next(x, lag=1, fill=None)` | `x` from `lag` periods later; the last periods take `fill`. |
| `cumsum(x)`, `cumprod(x)` | Running total, running product. |
| `where(cond, if_true, if_false)` | Element-wise choice. |
| `min(a, b, ...)`, `max(a, b, ...)` | Element-wise; with one series, its minimum or maximum. |
| `abs(x)`, `round(x, ndigits=0)`, `clip(x, lower=None, upper=None)` | Element-wise. |
| `pmt(rate, nper, pv, fv=0, when=0)` | Annuity payment, Excel's sign convention. |
| `total(x)`, `sum(x)`, `mean(x)`, `first(x)`, `last(x)` | Reductions to a scalar. |
| `at(x, period)` | The value at one period, e.g. `at(ebitda, 2026)`. |
| `irr(cashflows)`, `npv(rate, cashflows)` | IRR; NPV with the first cash flow undiscounted. |
| `is_forecast()`, `is_historical()` | True in forecast or historical periods. |
| `period()`, `period_index()` | The axis labels (e.g. 2027), or 0, 1, 2, … |

- **Scalars broadcast** over a series' periods.
- **Roll-forwards use `prev`**: `cash = prev(cash, init=minimum_cash) + fcf` steps period by
  period. Reductions (`total`, `irr`, `last`, …) and `next` read every period, so they
  can't feed back into such a loop.
- **Same-period loops** (interest on average debt, which depends on the cash sweep, which
  depends on interest) are errors until their metrics are declared in a circularity.
- **Missing values** (out-of-scope or unfilled periods) propagate as missing.

## Diagnostics

`validate` and every `apply_batch` result report problems with a `severity`, a `code`, a
`message` and the `object` concerned, e.g. `metric:sga`. Errors block a commit.

| Code | Usual fix |
| --- | --- |
| `unknown_reference` | A misspelled or missing metric: use a suggestion, or create it. |
| `circular_reference` | Use `prev()` if the loop should cross periods; otherwise declare a circularity. |
| `not_converged` | Check the loop; raise `max_iter` or set `damping` below 1. |
| `noncausal_recurrence` | Move the reduction (`total`, `irr`, `next`) out of the loop. |
| `recurrence_axis` | Give the metric an `axis`, or set `settings.default_axis`. |
| `axis_mismatch` | Align axes, or reduce one side to a scalar. |
| `unit_mismatch` | Convert, or correct a `unit`. |
| `syntax_error`, `disallowed_syntax`, `invalid_argument` | Fix the formula; `list_builtins` shows signatures. |
| `unknown_function` | Add the function, or fix the name. |
| `function_error`, `timeout`, `load_error` | Fix the custom function. |
| `calc_error` | The message names the period and metric. |
| `missing_value`, `nonfinite_value` | Often a missing `init`, or a division by zero. |
| `check_failed` | Fix the model, not the check. |
| `unknown_source`, `missing_file`, `unmatched_labels` | Check the file, sheet, range and labels. |
