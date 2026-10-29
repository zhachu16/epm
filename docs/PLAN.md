# fm — Implementation Plan

An AI-native alternative to Excel for financial modelling. Models are named quantities with
formulas, evaluated by a pandas engine, edited through an IDE-style local web app, a CLI (`fm`),
and a structured API that agents can drive.

## Decisions

| Topic | Decision |
| --- | --- |
| Delivery | Local web app. `fm serve` starts FastAPI and serves a React UI in the browser. |
| Formula language | A whitelisted subset of Python expressions, parsed with `ast` and compiled to vectorized pandas operations. |
| AI integration (v1) | CLI and HTTP API only. Agents propose change batches; the UI and CLI review, commit, roll back or undo them. No in-app chat. |
| Circularity | Undeclared cycles are rejected and the error shows the loop path. Declared cycles are solved by fixed-point iteration. |
| Archive extension | `.epm` (an ordinary ZIP). |
| Undo history | Session only. It lives in memory in the `fm serve` process and is never written to the archive. |
| Debt tranches | Separate 1-D metrics per tranche (`tlb_close`, `notes_close`, `revolver_close`). Totals are explicit formulas. |
| Stack | Python 3.12+, pandas, numpy, openpyxl, pyarrow, FastAPI, Typer, pydantic v2. Frontend: React, TypeScript, Vite, Monaco, React Flow + elkjs, ECharts, xterm.js, react-resizable-panels, TanStack Query. |

## Repository layout

```text
fm/                     Python package
  model/                pydantic schemas for every object kind, and model.json I/O
  formula/              parser, whitelist, AST → dependency extraction, compiler
  engine/               graph, SCC/topological ordering, evaluator, solver, cache
  sources/              CSV/XLSX loaders and range/column/index mapping
  functions/            custom-function registry and the sandboxed worker pool
  ops/                  structured operations, batches, validation, history
  analysis/             run, sweep, analyses and checks
  package/              ZIP archive read/write and fingerprints
  api.py                the shared Python API (Workspace class)
  cli/                  Typer app: `fm ...`, every command supports --json
  server/               FastAPI routes, websocket events, file watcher
web/                    React frontend
examples/lbo/           complete sample LBO model and its source files
tests/                  pytest suites, golden values for the LBO
docs/
```

## 1. Model format

The package is an ordinary ZIP archive with the extension `.epm`:

```text
model.json        definitions (authoritative)
inputs/           source files (authoritative)
functions.py      optional custom functions (authoritative)
cache/            optional, regenerable: <fingerprint>.parquet + index.json
```

A sketch of `model.json`:

```json
{
  "settings": { "name": "Project Atlas LBO", "currency": "USD", "default_axis": "fy" },
  "axes": {
    "fy": { "kind": "period", "values": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
            "historical": [2023, 2024, 2025], "label": "Fiscal year" }
  },
  "sources": {
    "historicals": { "file": "inputs/historicals.xlsx", "sheet": "IS", "range": "A1:J40",
                     "index": { "column": "A" }, "axis": "fy", "orientation": "rows" }
  },
  "metrics": {
    "revenue": {
      "axis": "fy", "unit": "USD m", "folder": "Income Statement",
      "history": { "source": "historicals", "row": "Revenue" },
      "formula": "prev(revenue) * (1 + revenue_growth)"
    },
    "revenue_growth": { "unit": "%", "value": 0.05, "folder": "Assumptions" },
    "interest_expense": {
      "axis": "fy", "unit": "USD m",
      "formula": "interest_rate * (prev(debt_close, init=opening_debt) + debt_close) / 2"
    }
  },
  "circularities": {
    "debt_sweep": { "metrics": ["interest_expense", "cash_sweep", "debt_close"],
                    "seed": 0, "tol": 1e-6, "max_iter": 100 }
  },
  "tables": {
    "income_statement": { "axis": "fy", "rows": [
      { "metric": "revenue" }, { "metric": "ebitda", "style": "subtotal" },
      { "header": "Below EBITDA" }, { "metric": "ebit" } ] }
  },
  "analyses": { "rate_sensitivity": { "...": "see section 6" } },
  "checks": {
    "balance_sheet_balances": { "expr": "total_assets - total_liabilities_and_equity",
                                "tolerance": 0.001, "severity": "error" }
  }
}
```

Notes:

- **Metric kinds.** A metric is defined by exactly one of: `value` (a scalar or a per-period
  mapping), `input` (a source binding), or `formula`. As an LBO convenience, a period-axis
  metric may combine a `history` binding with a `formula` that applies to forecast periods only.
- **Organization.** `folder` (and optional `tags`) drive the explorer tree. Everything still
  lives in one JSON file.
- **Writes.** The archive is written atomically: write a temp file, fsync, rename. Key order and
  formatting are stable so diffs stay readable.

## 2. Formula language

Formulas are Python expressions validated against a whitelist:

- **Allowed syntax.** Names, numeric and string literals, `+ - * / ** %`, unary minus,
  comparisons, `&`, `|`, `~`, calls to whitelisted functions, and calls to custom functions.
  No attribute access, subscripts, lambdas or comprehensions.
- **Built-ins:**
  - Time: `prev(x, lag=1, init=...)`, `next(x)`.
  - Logic: `where(cond, a, b)`.
  - Elementwise: `min`, `max`, `abs`, `round`, `clip`.
  - Reductions to scalar: `total`, `mean`, `first`, `last`, `at(x, 2028)`.
  - Cumulative: `cumsum`, `cumprod`.
  - Period helpers: `is_forecast`, `is_historical`, `period`, `period_index`.
  - Finance: `irr(cashflows)`, `xirr`, `npv(rate, cashflows)`, `pmt`.
- **Dependencies.** Every free name is a metric reference. The dependency extractor records
  each edge with its lag (0 for a same-period reference, k for `prev(x, lag=k)`).
- **Compilation.** Formulas compile to a Python closure over a context of pandas Series and
  scalars. Scalars broadcast to the axis. Mixing different axes is a validation error unless a
  reduction is applied.
- **Units.** Units are lightweight tags such as `USD m`, `%`, `x`, `count` and `years`. `+`,
  `-` and comparisons between different units produce a warning, or an error in strict mode.
  `*` and `/` are unchecked in v1 but displayed.

## 3. Engine

1. **Graph.** Nodes are metrics, edges are labelled with their lag. Validation checks for
   unknown names, axis mismatches, unit warnings, sources that don't resolve, and missing values
   in required periods.
2. **Ordering.** Compute strongly connected components on the **same-period** edges (lag 0).
   - An SCC with more than one node, or a self-loop, must exactly match a declared
     `circularities` block. Otherwise the model is rejected and the error shows the path.
   - Topologically sort the condensed DAG.
3. **Evaluation strategies** for each node or block, in order:
   - **Vectorized.** The default: the whole Series is computed at once. Most metrics use this.
   - **Recurrence.** Metrics joined by lagged edges, such as `cash = prev(cash) + fcf` or a debt
     roll-forward, are grouped into a recurrence group and stepped period by period. The values
     inside a step stay scalar-fast. Simple self-recurrences of the form `prev(x) + y` are
     rewritten to `init + cumsum(y)` so they stay vectorized.
   - **Circular block (solver).** Within each period, apply fixed-point iteration over the
     declared metrics: seed the values, evaluate the block in a fixed internal order, and repeat
     until max |Δ| < tol or `max_iter` is reached. The engine records iteration count and
     residual for each period. If the block fails to converge, or produces a NaN or inf, it is
     reported as a calculation error on those metrics. It never fails silently. Optional damping
     is available. A circular block that also has lags is solved inside the recurrence stepper.
   - **Historical periods.** Metrics with a `history` binding take source values for historical
     periods. The formula covers forecast periods. The `init` of `prev()` defaults to the last
     historical value when one exists.
4. **Selective recalculation.** Each metric fingerprint is a hash of its definition, its
   upstream fingerprints, the source-file hashes, and the hashes of the custom functions it
   calls. After an edit, only metrics whose fingerprints changed are recomputed (the edited node
   and everything downstream of it). Values are kept in an in-memory cache and optionally
   persisted to `cache/` as parquet, keyed by fingerprint.
5. **Status.** Each metric reports a status: `ok`, `stale`, `error` (with a message), or
   `solved` (with the iteration count). The inspector shows it.

## 4. Custom functions

- **Declaration** in `functions.py`:

  ```python
  from fm import function

  @function(returns="series", axis="fy")
  def amortization_schedule(principal, rate, years):
      ...
  ```

- **Calls.** Formulas call functions by name, such as
  `amortization_schedule(tlb_amount, tlb_rate, tlb_tenor)`. Dependencies come only from the
  arguments. The function sees no model context, so tracing stays complete.
- **Multiple outputs.** A function may return a dict. Metrics bind its outputs with
  `{"function_output": {"call": "debt_waterfall(...)", "key": "interest"}}`, and the engine
  executes each call once.
- **Isolation.** Functions run in a pool of worker processes (`multiprocessing` with the
  `spawn` start method). Each worker sets `resource.setrlimit` limits for CPU time, address
  space and open files. The parent also enforces a wall-clock timeout and kills workers that
  exceed it. Workers have a restricted `__builtins__` with no network modules (best effort).
  Arguments and results are pickled pandas or numpy objects. On Windows, timeouts and memory
  limits are enforced through job objects, or only the timeout applies. Per-call cost is
  around 1 ms, so calls are batched per period-block where possible.

## 5. Operations, batches and history

All three surfaces, the UI, the CLI and agents, go through one ops layer:

```json
{ "ops": [
  { "op": "update", "kind": "metric", "name": "interest_rate", "set": { "value": 0.06 } },
  { "op": "create", "kind": "metric", "name": "fees", "def": { "value": 12, "unit": "USD m" } },
  { "op": "rename", "kind": "metric", "name": "old_name", "to": "new_name" },
  { "op": "delete", "kind": "metric", "name": "unused_metric" }
]}
```

- **Kinds:** `metric`, `axis`, `source`, `function`, `table`, `analysis`, `check`,
  `circularity`, `settings`.
- **Atomic batches.** A batch is applied to a copy of the model, then the full model is
  validated. It commits only if everything passes; otherwise the whole batch is rejected with
  per-op errors.
- **Rename** rewrites references with an AST-preserving rewrite of every formula, table,
  analysis, check and circularity that uses the name.
- **Delete** fails while references remain and lists them. `--force` is not offered in v1.
- **Previews.** Any batch can be previewed without committing (`fm apply changes.json
  --dry-run`). A preview shows the definition diff, and the old value, new value and Δ for
  selected outputs (by default, the outputs of the model's key analyses and checks).
- **Session history (in memory only).** History lives in the session, not in the archive.
  - The `fm serve` process owns the session. It keeps each committed batch with its inverse for
    undo, redo and rollback, and holds **pending** batches (`fm apply changes.json --propose`)
    for the user to commit or reject in the UI or CLI.
  - When a session is running, the CLI finds it through a lock file next to the archive
    (`.atlas.epm.lock`, holding the port and a token). It then sends commands to the server
    instead of editing the file directly, so CLI and agent edits join the same undo stack and
    appear in the UI live.
  - Without a running session, each CLI command is its own one-batch session. Edits commit
    straight to the archive, `--propose` behaves like `--dry-run`, and `fm undo`/`fm redo` report
    that no session is active.
  - History is lost when the server stops. The archive holds only definitions, inputs,
    functions and cache.

## 6. Run, sweep, analyses

Python API:

```python
ws = Workspace.open("atlas.epm")
ws.run(overrides={"interest_rate": 0.08}, outputs=["equity_irr"])
ws.sweep(inputs={"entry_multiple": [9, 10, 11], "exit_multiple": [9, 10, 11]},
         outputs=["equity_irr", "moic"], mode="grid")   # or mode="zip"
```

- **Overrides** apply to a temporary copy. Base assumptions are never modified.
- **Sweeps.** Each scenario computes only the part of the graph downstream of the swept inputs,
  reusing the base cache for everything else. Scenarios run in parallel with a process pool.
  The result is a DataFrame with one row per scenario: the input columns, the output columns,
  and `status` and `error`. A failed scenario doesn't abort the sweep.
- **Analyses** store their inputs, values, outputs, mode and plot settings: `line` (one input
  on x, outputs on y) or `heatmap` (two inputs, one output, a format string). Re-running an
  analysis uses the current model. Results are cached by model fingerprint, so a rerun after
  an edit is automatic.
- **Checks** are evaluated on every run and appear in validation output and the UI.

## 7. CLI (`fm`)

The CLI is built with Typer. `fm` uses the `.epm` in the current directory, or `-m PATH`.
`--json` returns a structured envelope: `{ok, data, errors, warnings}`.

```text
fm init NAME
fm list metrics|sources|functions|tables|analyses|checks [--folder F] [--search Q]
fm show NAME
fm metric set NAME (--value V | --formula F | --input SRC:ROW) [--unit U] [--axis A]
fm metric rename OLD NEW
fm metric delete NAME
fm source add|set|delete ...
fm function add|set|delete ...   # also: fm function edit (opens $EDITOR)
fm table ... / fm analysis ... / fm check ...
fm trace NAME --upstream|--downstream [--depth N]
fm validate
fm run [--set k=v ...] [--output a,b]
fm sweep --input "k=v1,v2" [--input ...] --output X [--mode grid|zip]
fm analysis run NAME [--plot out.png]
fm apply changes.json [--dry-run | --propose]
fm pending list|show|commit|reject ID
fm history | fm undo | fm redo | fm rollback ID   # need a running fm serve session
fm pack DIR / fm unpack FILE DIR
fm serve [--port 8765]
```

## 8. Server

The HTTP API mirrors the Python API: `GET /objects`, `GET /objects/{kind}/{name}`,
`POST /batches` (`propose` or `commit`), `POST /run`, `POST /sweep`,
`POST /analyses/{name}/run`, `GET /trace/{name}`, `GET /graph`, `GET /validate`, and
history endpoints.

- **Live updates.** A websocket at `/events` pushes `model_changed`, `calc_progress`,
  `pending_added` and log events. A file watcher detects CLI or agent edits to the archive, so
  the UI updates live.
- **Terminal.** `/terminal` is a websocket that runs `fm` commands only, parsed and dispatched
  in-process. It is not a raw shell.

## 9. Frontend

- **Left (explorer).** A searchable tree of folders → inputs, metrics, tables, functions,
  analyses, checks and sources, with fuzzy search and badges for status and errors.
- **Centre (tabs).**
  - Metric editor: a form view, plus a formula field in Monaco with name completion, hover and
    go-to-definition.
  - Table editor: a grid with drag-to-reorder rows, rendered with values.
  - `functions.py` and `model.json` text editors with schema validation.
  - Source mapping editor with a preview of the mapped range.
  - Analysis editor and chart.
  - Visual and text editing round-trip through the ops layer.
- **Default view (graph).** React Flow with an elkjs layered layout. Nodes are metrics; table
  groups are collapsible compound nodes. Hovering an edge or node shows its formula; clicking
  opens its definition. Circular blocks are highlighted, and the upstream or downstream of a
  selection can be filtered.
- **Right (inspector).** For the selected metric: value (a sparkline and table for a series),
  definition and source, axis, unit, dependencies and dependants (both clickable), status and
  solver stats.
- **Bottom panel.** Terminal (xterm.js), logs, validation problems, and the **Changes** tab:
  pending batches with a definition diff, output Δ, and commit or reject buttons, plus
  undo, redo and history.
- **Analyses UI.** Pick inputs, value lists or ranges, outputs and mode. ECharts renders line
  plots and heatmaps. Analyses can be saved and re-run.

## 10. Sample LBO (`examples/lbo`)

- **Inputs.** `historicals.xlsx` (3 years of income statement and balance sheet) and
  `assumptions.csv`.
- **Transaction.** Entry EV from LTM EBITDA × entry multiple; sources and uses (TLB, senior
  notes, sponsor equity, fees, cash to balance sheet), with a check that sources equal uses.
- **Operations.** Revenue growth, EBITDA margin, D&A, capex and NWC as % of revenue, and taxes.
- **Statements.** Integrated income statement, balance sheet and cash-flow statement, with a
  balance check for every period.
- **Debt.** Each tranche (TLB, senior notes, revolver) is its own set of 1-D metrics
  (`tlb_open`, `tlb_interest`, `tlb_repayment`, `tlb_close`, and so on), and `total_debt`,
  `total_interest` and similar totals are explicit formulas. Mandatory TLB amortization comes from a
  custom function. A cash sweep follows a priority waterfall (revolver, then TLB) with
  **average-balance interest**. This uses the declared circularity
  `interest ↔ sweep ↔ debt_close`.
- **Returns.** Exit EV, net debt, equity value, IRR and MOIC for 3- and 5-year exits.
- **Saved analyses.** An entry × exit multiple heatmap of IRR, an interest-rate line plot, and a
  leverage sensitivity.
- **Golden tests.** Values are cross-checked against an independently built spreadsheet
  calculation, held to a tolerance.

## 11. Milestones

| # | Deliverable | Done when |
| --- | --- | --- |
| M0 | Scaffold: package, pyproject, ruff, pytest, CI, Vite app | `pytest` and `npm run build` pass |
| M1 | Schemas, formula parser/compiler, graph, vectorized evaluator, `prev()` recurrences | Unit tests on a toy 3-statement model |
| M2 | Sources (CSV/XLSX mapping), history and forecast periods, units and axis validation | Errors for bad references, axes and units |
| M3 | Declared circular solver, with convergence reporting | Average-interest debt schedule converges; undeclared cycle rejected with path |
| M4 | Archive I/O, fingerprints, selective recalc, parquet cache | An edit recomputes only the downstream subset (asserted in tests) |
| M5 | Ops layer: atomic batches, rename/delete semantics, previews, in-memory session history | Property tests: apply followed by inverse is identity |
| M6 | Sandboxed function workers | Timeout, memory and CPU limits enforced in tests |
| M7 | run, sweep, analyses, checks, plus the full `fm` CLI with `--json` | Every listed command works end to end |
| M8 | Sample LBO and golden tests | Matches the reference values |
| M9 | FastAPI server as the session owner (undo, pending), CLI-to-server routing via lock file, websocket events, file watcher, terminal endpoint | API tests; a CLI edit while serving is undoable from the UI |
| M10 | UI shell, explorer, graph view, inspector | Graph renders the LBO; click-through works |
| M11 | Editors (metric, table, source, function, JSON), analyses UI, Changes panel | A full edit, review and commit loop in Playwright e2e |
| M12 | Hardening: performance pass (sweeps of 1k scenarios), docs, agent guide | Agent guide (`docs/AGENTS.md`) and CLI reference published |

The backend (M0–M9) is usable headless by agents before any UI work starts.

## 12. Risks

- **Recurrence speed.** Period stepping is slower than vectorized evaluation. Mitigations: the
  cumsum rewrite, keeping each step in numpy scalars, and only a few periods per model (about
  10–40).
- **Sandbox limits.** `setrlimit` is Unix-only, and Python-level sandboxing is not a security
  boundary. Treat `functions.py` as trusted-by-the-user code with resource limits, not as an
  untrusted-code sandbox. Document this clearly.
- **Solver convergence.** Use damping and a clear failure status, and fall back to
  opening-balance interest if needed.
