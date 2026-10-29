# epm — Implementation Plan

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
| Cloud delivery | A `service/` folder next to `epm/`, deployed together on Render as one long-running Docker container. The web frontend is hosted on Vercel. See section 12. |
| Cloud storage | Supabase Postgres for metadata (projects, versions, outputs); Cloudflare R2 for `.epm` archives, inputs and outputs. |
| Cloud auth | None during the alpha. The app and both APIs are open; Supabase Auth comes later. No API tokens or shared secrets. |
| Branches | `main` holds everything. `local-app`, `web-frontend` (Vercel) and `web-backend` (Render) are generated from it after CI passes, each with only the folders its target needs. |
| Cloud concurrency | Stateless: each request loads the project's latest version, applies, and saves a new version. Every saved version can be restored. |

## Repository layout

```text
epm/                    core Python package, shared by the local app and the cloud service
  model/                pydantic schemas for every object kind, and model.json I/O
  formula/              parser, whitelist, AST → dependency extraction, compiler
  engine/               graph, SCC/topological ordering, evaluator, solver, cache
  sources/              CSV/XLSX loaders and range/column/index mapping
  functions/            custom-function registry and the sandboxed worker pool
  ops/                  structured operations, batches, validation, history
  analysis/             run, sweep, analyses and checks
  package/              ZIP archive read/write and fingerprints
  workspace.py          the shared Python API (Workspace class)
  cli/                  Typer app: `fm ...`, every command supports --json
  server/               `fm serve`: the local session server
service/                the cloud service (HTTP + MCP) over epm; `service.py` runs it
frontend-local/         the local app's UI, served by `fm serve` (standalone Vite package)
frontend-web/           the cloud web app, hosted on Vercel (standalone Vite package)
examples/lbo/           complete sample LBO model and its source files
tests/                  pytest suites for epm, golden values for the LBO
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
                     "orientation": "rows", "axis": "fy" },
    "deal": { "file": "inputs/historicals.xlsx", "sheet": "Deal" }
  },
  "metrics": {
    "revenue": {
      "axis": "fy", "unit": "USD m", "folder": "Income Statement",
      "history": { "source": "historicals", "item": "Revenue" },
      "formula": "prev(revenue) * (1 + revenue_growth)"
    },
    "revenue_growth": { "unit": "%", "value": 0.05, "folder": "Assumptions" },
    "entry_multiple": { "unit": "x", "input": { "source": "deal", "cell": "B4" } },
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
- **Sources.** `orientation: "rows"` (financial-statement layout) treats each row as a series.
  The `index` row (a row number) holds axis labels and the `labels` column (a letter) holds
  item names. `orientation: "columns"` (tabular layout) is the transpose: the `index` column
  holds axis labels and the `labels` row holds headers. Both default to the first row and
  column of `range`, which defaults to the whole sheet.
  - Bindings pick a series by `item`, or one value by `cell` (`"B4"` or `"Deal!B4"`), with an
    optional `scale`.
  - Text cells accept thousands separators, currency symbols, `(123)` negatives and `%`. A
    lone dash is zero; `n/a` and blanks are missing.
  - Labels match axis values by text (`2024.0` matches `2024`). Source labels that aren't on
    the axis are ignored and reported as info.
  - XLSX formulas use Excel's saved values. A formula cell with no saved value is a warning.
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
  `-`, comparisons, and functions that combine like values (`where` branches, `min`/`max`,
  `prev` with `init`, reductions) warn on different units, or give an error with
  `settings.strict_units`. `*` and `/` are unchecked in v1. Undeclared units are inferred,
  and a formula whose unit differs from the declared one is flagged. Spelling is normalized
  for comparison (`USD m` = `usd  M`).

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
  from epm import function

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

`fm serve` owns the session: the loaded model, its cache, undo and redo history, and pending
proposals. It listens on localhost only. Requests must carry a localhost `Host` header (against
DNS rebinding) and, except `/api/health`, the session token, which the served `index.html`
embeds. A lock file (`.epm-session.lock` next to the model) records the port and token, so
`fm` commands for that model are forwarded to the session instead of editing the files
underneath it, and share its undo history.

The HTTP API mirrors the Python API: `GET /api/state` (model, values, statuses, checks,
diagnostics, history and pending in one snapshot), `GET /api/trace/{name}`,
`GET /api/sources/{name}/preview`, `POST /api/batches` (`commit`, `dry_run` or `propose`),
`PUT /api/model` and `PUT /api/functions` (whole-file edits, diffed into ops),
`POST /api/pending/{id}/commit|reject`, `POST /api/undo`, `/api/redo`,
`/api/rollback/{id}`, `POST /api/run`, `/api/sweep`, `/api/analyses/{name}/run` and
`POST /api/cli`.

- **Live updates.** A websocket at `/api/events` pushes `model_changed` and log events. A
  file watcher reloads the model when it changes on disk outside the session.
- **Terminal.** `/api/terminal` is a websocket that runs `fm` commands only, parsed and
  dispatched in-process. It is not a raw shell.

## 9. Frontend

A standalone Vite + React + TypeScript app in `frontend-local/` (formerly `frontend/`),
served by `fm serve` from `frontend-local/dist`. The cloud web app is a separate package,
`frontend-web/` (section 12.8).

- **Left (explorer).** A searchable tree of folders → inputs, metrics, tables, functions,
  analyses, checks, sources, circularities, axes and files, with status badges, and a
  **New** menu for each kind of object.
- **Centre (tabs).**
  - Metric editor: a form (formula, value, per-period values or source binding; unit, axis,
    scope, folder), with the formula in Monaco with completion of metrics, built-ins and
    functions, and hover showing values.
  - Table editor: rows with labels, styles, headers and reordering, rendered with values.
  - Function editor, plus `functions.py` and `model.json` text editors.
  - Source mapping editor with file upload and a preview of what metrics can bind to.
  - Analysis editor: inputs as value lists or ranges, outputs, grid or paired mode, a line or
    heatmap chart (ECharts) and a results table.
  - JSON editors for checks, circularities, axes and settings.
  - Every editor has **Preview** (a dry run shown in Changes) and **Save**; all edits go
    through the ops layer.
- **Default view (graph).** React Flow with an elkjs layered layout. Nodes are metrics; table
  groups are collapsible compound nodes, collapsed into an overview for large models.
  Hovering a node or edge shows its formula and value; clicking opens the metric. Circular
  members are marked, and the graph can be filtered to the upstream or downstream of a
  selection.
- **Right (inspector).** For the selected metric: value (a sparkline and table for a series),
  definition, unit, axis, dependencies and dependants (both clickable), status and
  fingerprint.
- **Bottom panel.** **Changes** (the latest preview and agents' proposals, each with a
  definition diff and its effect on key outputs, commit or reject, and the session history
  with rollback), problems, an `fm` terminal (xterm.js) and logs. Undo and redo sit in the
  top bar.

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

| # | Deliverable | Status |
| --- | --- | --- |
| M0 | Python scaffold: package, pyproject, ruff, pytest, CI | done |
| M1 | Schemas, formula parser/compiler, graph, vectorized evaluator, `prev()` recurrences | done |
| M2 | Sources (CSV/XLSX mapping), history and forecast periods, units and axis validation | done |
| M3 | Declared circular solver with convergence reporting; forecast-only metric scope | done |
| M4 | Archive I/O, fingerprints, selective recalc, parquet cache, `Workspace` API | done |
| M5 | Ops layer: atomic batches, rename/delete semantics, previews, in-memory session history | done |
| M6 | Custom functions in a sandboxed worker (CPU, memory, timeout, import allowlist) | done |
| M7 | run, sweep, analyses, checks, plus the `fm` CLI with `--json` | done |
| M8 | Sample LBO (`examples/lbo`) and golden tests against an independent calculation | done |
| M9 | FastAPI server as the session owner (undo, pending), CLI-to-server routing via lock file, websocket events, file watcher, terminal endpoint | done |
| M10 | Vite app scaffold, UI shell, explorer, graph view, inspector | done |
| M11 | Editors (metric, table, source, function, JSON), analyses UI, Changes panel, terminal, Playwright end-to-end tests | done |
| M12 | Hardening: performance pass (sweeps of 1k scenarios), docs, agent guide | done |
| M13 | Repo split: `frontend/` → `frontend-local/`; the minimal epm changes for the cloud (12.2) | done |
| M14 | Service storage: store interfaces, Supabase and R2 implementations, in-memory fakes, `schema.sql`, versions, example seeding | done |
| M15 | Service layer and HTTP API: projects, batches, runs, files, outputs, versions, undo | done |
| M16 | MCP endpoint over the same service layer | done |
| M17 | `frontend-web`: projects page, project view, autosave status, versions, files and outputs, share links, example copy, docs page | done |
| M18 | Docs: user guide with HTTP and MCP usage and the onboarding example, all tested | done |
| M19 | Deployment: Dockerfile and Render blueprint, Vercel config, deployment branches, smoke test, CI jobs | done |

The backend (M0–M9) is usable headless by agents before any UI work starts. M13–M19 were
built on one branch, then promoted to `main`.

### Notes from building M0–M8

- **Forecast scope.** Metrics can be `"scope": "forecast"`, declared or inferred from what
  they read. This keeps post-close balances (debt, cash, equity) blank in historical periods
  without warnings. `prev(x, init=...)` starts at the first forecast period, and reductions
  skip history.
- **Default axis.** A metric that reads its own previous period defaults to
  `settings.default_axis`.
- **Undeclared loops.** The circular-reference error lists every member of the loop, ready
  to copy into a `circularities` block.
- **Function worker.** The worker is a `python -I -m epm.functions.worker` subprocess
  talking framed pickle over pipes. `multiprocessing` spawn would re-import the caller's
  main script. Module-level imports in function code are moved to the top of
  `functions.py`.
- **Session commands.** CLI history, undo, redo, rollback and pending commands need a
  running `fm serve`; without one they say so.
- **Cache directory.** Unpacked model directories write `cache/`; it is git-ignored.

### Notes from building M9–M11

- **Shutdown.** uvicorn re-raises SIGTERM and SIGINT after it stops, so code after
  `server.run()` never runs. Session cleanup (lock file, cache, function worker) happens in
  the app's lifespan instead. Websocket handlers must also watch for disconnects, or shutdown
  waits on them forever.
- **Forwarded commands.** `fm` commands forwarded to a session run in-process against the
  session's workspace, so they share its undo history and pending proposals. `--help`,
  `serve`, `init`, `pack` and `unpack` always run locally.
- **Graph overview.** Models with more than 40 metrics open with their groups collapsed;
  expanding a group zooms to it.
- **Monaco.** Bundled locally with only the editor core, Python and JSON, since the UI runs
  offline next to the model.

### Notes from M12

Timings from `python benchmarks/run.py` on the sample LBO, per 1,024-scenario sweep:

| Sweep | Before | After |
| --- | --- | --- |
| Entry × exit multiples (debt schedule reused) | 6.7 s | 1.6 s |
| Interest rates (circular debt schedule re-solved each time) | about 125 s | 4.4 s |

Edits preview and commit in about 25 ms. A synthetic model of 10,000 metrics builds in about
1 s and runs in about 2 s.

- **Period-by-period evaluation.** Recurrences used to evaluate each formula over the whole
  series at every period and solver iteration, then keep one value. `formula/point.py`
  instead evaluates the parts of a formula that read the recurrence's own metrics at one
  position with Python floats. Parts that don't are evaluated once per run over the whole
  series, and custom functions fall back to whole-series evaluation. Anything beyond plain
  float arithmetic goes through numpy scalars, so the results match exactly;
  `tests/test_point.py` checks this on zeros, signed zeros, NaN and infinities.
- **One engine per sweep.** Every scenario of a sweep overrides the same metrics, so a sweep
  builds one overridden engine and changes only the values between scenarios
  (`Engine.set_values`). Fingerprints are recomputed from cached per-metric hashes.
- **Sweep caching.** Sweeps read the shared value cache through an overlay that keeps their
  own results, so a large sweep reuses the model's cached steps without evicting them.
- **Worker start.** The function worker starts loading `functions.py` when the engine is
  built, so its startup overlaps with reading sources.
- **Docs are executable.** `tests/test_docs.py` runs every batch, command and Python
  example in the README and the [agent guide](agent-guide.md).

## 12. Cloud service (alpha)

A hosted version of epm: a web app on Vercel, and a service on Render that runs the epm
engine behind an HTTP API and an MCP endpoint. Projects live in Supabase (metadata) and R2
(files). The local app (`fm`, `fm serve`, `frontend-local`) keeps working as it does today.

### 12.1 Principles

- **epm is the core.** `service/` imports `epm` and reuses `Workspace` for inspection,
  editing and evaluation. epm changes only where the cloud needs it (12.2).
- **One service layer.** HTTP routes and MCP tools are thin wrappers over the same
  functions in `service/projects.py`. All model edits still go through `Workspace.apply`
  (op batches), including uploaded input files (`file` ops).
- **Stateless requests.** A request loads the project's current version, does its work and,
  if it changed the model, saves a new version. No session, no in-memory undo stack, no
  pending proposals in the cloud. Undo is restoring an earlier version (12.5).
- **Open alpha.** No authentication and no secrets. Every route takes an `Actor` from one
  FastAPI dependency, `get_actor()`, which returns an anonymous actor for now; Supabase Auth
  replaces it later without touching the routes.
- **Hard-coded config.** Supabase and R2 credentials are placeholders in
  `service/config.py`, filled in by hand for the MVP. Nothing reads them from the
  environment.

### 12.2 Changes to epm

Kept minimal:

- **Snapshot.** Move the body of `Session.snapshot` into a function,
  `epm.server.session.workspace_snapshot(ws, path)`, that `Session.snapshot` calls. The
  service returns the same shape, so `frontend-web` reuses the local UI's types and
  editors.
- **Archives in memory.** `read_package_bytes(data)` and `package_bytes(pkg,
  include_cache)` next to `read_package` and `write_package`, so the service can move
  `.epm` archives to and from R2 without temp files. Existing behaviour is unchanged.
- **Frontend path.** `find_frontend` looks in `frontend-local/dist`; messages and docs that
  name `frontend/` are updated.

### 12.3 Layout

```text
service/
  service.py          entry point: builds the stores from config and the FastAPI app;
                      `python -m service.service [--memory]`
  config.py           hard-coded Supabase URL and key, R2 account, keys and bucket (placeholders)
  projects.py         the service layer: projects, versions, batches, runs, files, outputs
  api.py              HTTP routes under /api
  tools.py            MCP tools, mounted at /mcp
  actor.py            get_actor(): the auth seam, anonymous for now
  store/
    base.py           MetaStore and BlobStore interfaces
    supabase.py       MetaStore on Supabase (supabase-py)
    r2.py             BlobStore on R2 (boto3, S3 API)
    memory.py         in-memory fakes for tests and local runs
  schema.sql          Supabase tables and the commit_version function
  requirements.txt    the service's dependencies on top of epm (supabase, boto3, mcp)
  Dockerfile          builds epm and the service; build context is the repo root
  tests/              pytest suites against the in-memory stores
```

`service.py` creates the app with real stores; `create_app(meta, blobs)` takes any store,
so tests and a local `python -m service.service --memory` run use the in-memory fakes.

### 12.4 Storage

**Supabase (Postgres)**

| Table | Columns |
| --- | --- |
| `projects` | `id` (text, e.g. `p_7f3k9q2m`), `name`, `description`, `is_example`, `current_version`, `created_at`, `updated_at`, `deleted_at` |
| `versions` | `project_id`, `number`, `blob_key`, `label`, `source` (`create`, `ui`, `mcp`, `import`, `restore`), `ops`, `bytes`, `sha256`, `created_at`; unique `(project_id, number)` |
| `outputs` | `id`, `project_id`, `version`, `kind` (`run`, `sweep`, `analysis`, `plot`), `name`, `blob_key`, `content_type`, `bytes`, `created_at` |

- **Commits are atomic.** The archive is uploaded to R2 first under a new key, then the
  Postgres function `commit_version(project_id, expected_version, ...)` inserts the version
  row and moves `current_version` only if it still equals `expected_version`. Otherwise it
  fails with `version_conflict` and the orphaned blob is removed.
- **Search** matches `name` and `description` (case-insensitive), newest first, excluding
  deleted projects.

**R2**

```text
projects/{project_id}/versions/{number}.epm    every saved version (without cache/)
projects/{project_id}/outputs/{output_id}.{csv,json,png}
backups/meta/{date}.json                       daily export of the three tables
```

Version blobs are immutable, so the service keeps a small on-disk cache of them in the
container. Calculated values are kept in an in-process `ValueCache` per project; it is safe
across versions because step fingerprints key it, and losing it on a redeploy only costs
time. Nothing authoritative lives on the container's disk.

### 12.5 Service layer

`service/projects.py`, used by both HTTP and MCP:

| Area | Functions |
| --- | --- |
| Projects | `create(name, from_epm=None)`, `list(q=None)`, `get(id)`, `rename(id, name)`, `duplicate(id, name)`, `delete(id)`, `import_epm(bytes, name)`, `export_epm(id, version=None)` |
| Model | `state(id)` (the UI snapshot), `show`, `trace`, `validate`, `list_objects(kind)` |
| Edits | `apply(id, ops, mode="commit" \| "dry_run", expected_version=None, label=None, source=...)` |
| Evaluation | `run(id, overrides, outputs)`, `sweep(id, inputs, outputs, mode)`, `run_analysis(id, name, plot=False)` |
| Files | `list_files(id)`, `download_file(id, path)`; uploads and removals are `file` ops through `apply` |
| Outputs | `save_output(...)`, `list_outputs(id)`, `download_output(id, output_id)` |
| Versions | `list_versions(id)`, `restore(id, number)`, `undo(id)` |

- **Project IDs.** Every project gets a unique `project_id` at creation. Every operation on
  an existing project requires it; only create and list/search don't.
- **Saving.** A committed batch, an import, a file upload or a restore writes a new version.
  `dry_run` never writes. Validation errors block a commit, as locally.
- **Conflicts.** With `expected_version`, a write fails with `version_conflict` if someone
  saved in between; the web app always sends it. Without it (agents), the write applies to
  the latest version and the service retries once if a commit lands in between.
- **Undo and restore.** `restore(id, n)` saves version *n*'s contents as a new version, so
  history is never rewritten and a restore can itself be undone. `undo(id)` restores the
  version before the current one. Redo is restoring the later version.
- **Example project.** `example-lbo` is seeded from `examples/lbo` at startup (a new version
  when the files change). Every write to it fails with `read_only_project`; `duplicate`
  makes an editable copy.
- **Delete** is soft: a deleted project disappears from lists, is kept for 30 days, and can
  be restored with `restore_project(id)`.
- **Retention.** A daily job keeps the last 50 versions of each project plus one per day for
  90 days, purges projects deleted more than 30 days ago, and writes `backups/meta/`. R2
  object versioning is turned on for the bucket.
- **Errors** use epm's codes and messages: model problems are diagnostics in results, bad
  requests raise `EpmError` (`unknown_project`, `read_only_project`, `version_conflict`).

### 12.6 HTTP API

All under `/api`, JSON in and out, errors as `{code, message}`. CORS allows any origin.

| Method and path | Purpose |
| --- | --- |
| `GET /projects?q=` · `POST /projects` | List and search; create (empty, or `{"from": "example-lbo"}`) |
| `POST /projects/import?name=` | Upload an `.epm` (the request body) as a new project |
| `GET /projects/{id}` · `PATCH /projects/{id}` · `DELETE /projects/{id}` | Metadata; rename; delete |
| `POST /projects/{id}/duplicate` · `POST /projects/{id}/restore` | Copy; undelete |
| `GET /projects/{id}/export?version=` | Download the `.epm` |
| `GET /projects/{id}/state` | The UI snapshot (same shape as `fm serve`), plus `project` and `version` |
| `POST /projects/{id}/batches` | `{ops, mode, expected_version, label}` |
| `GET /projects/{id}/trace/{name}` · `GET /projects/{id}/sources/{name}/preview` | As locally |
| `POST /projects/{id}/run` · `/sweep` · `/analyses/{name}/run` | Evaluate; `save: true` stores the result (and plot) as an output |
| `GET /projects/{id}/files` · `GET /projects/{id}/files/{path}` | List and download inputs |
| `GET /projects/{id}/outputs` · `GET /projects/{id}/outputs/{oid}` | List and download outputs, including plot PNGs |
| `GET /projects/{id}/versions` · `GET /projects/{id}/versions/{n}/export` | History; download a version |
| `POST /projects/{id}/versions/{n}/restore` · `POST /projects/{id}/undo` | Recover |
| `GET /health` | Liveness for Render |

### 12.7 MCP

The official `mcp` Python SDK, streamable HTTP transport, mounted at `/mcp` on the same app.
Tools mirror the service layer; every tool except the first three requires `project_id`.

| Tool | Notes |
| --- | --- |
| `list_projects`, `search_projects`, `create_project` | `create_project` can copy the example |
| `get_project`, `rename_project`, `duplicate_project`, `delete_project` | |
| `list_objects`, `show`, `trace`, `validate`, `list_builtins` | Read the model |
| `apply_batch` | `ops`, `mode` (`commit` or `dry_run`), optional `expected_version`, `label` |
| `run`, `sweep`, `run_analysis` | Results as JSON; `save` stores an output |
| `list_files`, `list_outputs`, `export_project` | Exports return a download URL on the HTTP API |
| `list_versions`, `undo`, `rollback` | `rollback(project_id, version)` restores that version, so a person can simply ask the agent to undo or roll back |

Tool descriptions carry the ground rules from the agent guide (read before writing, preview
with `dry_run`, name every assumption).

### 12.8 Web frontend (`frontend-web/`)

A standalone Vite + React package, started from a copy of `frontend-local`'s editors,
graph, inspector and tables, with its own API client over `/api/projects/{id}/...`. The two
packages share no code, so either can change without breaking the other. `VITE_API_URL`
points it at the service; `vercel.json` rewrites routes to `index.html`.

- **Routes.** `/` projects, `/p/:projectId` a project, `/docs` the user guide.
- **Projects page.** Search, create, open, rename, duplicate, delete, and import or export
  `.epm` files. The example project is pinned at the top.
- **Project view.** The local UI's layout (explorer, graph, editors, inspector, bottom
  panel) without the terminal, proposals and undo/redo buttons.
- **Autosave.** Form editors commit on save, and the text editors (`model.json`,
  `functions.py`) commit after a pause in typing, each with `expected_version`. The top bar
  shows the save state: saving, saved (version and time), conflict (reload, keeping the
  unsaved text to copy), or error.
- **Versions panel.** Each version's label, source and time, with download and restore.
- **Files and outputs.** Upload and download inputs; download run results, sweep results
  and analysis plots (PNG) saved as outputs.
- **Example project.** A read-only banner and a **Make an editable copy** button; editors
  are disabled.
- **Share.** A **Copy link** button gives `/p/{project_id}`. During the alpha anyone with
  the link can open and edit the project; once Supabase Auth lands, the same link asks the
  recipient to sign in first. An `AuthGate` component already wraps the routes and lets
  everyone through.
- **Docs page.** Renders `docs/user-guide.md`, copied into the bundle by a prebuild script
  so the package builds on its own.

### 12.9 Docs

- **`docs/user-guide.md`** (new, shown in the web app): modeling conventions, the projects
  workflow, project IDs, sharing links, import and export, files and outputs, autosave and
  recovering versions, the HTTP API and MCP with examples, and the alpha's open access.
- **Onboarding example**, end to end: open the app → copy the example project → change an
  assumption → run the model → inspect and export results (`.epm`, results, plot) → connect
  an MCP client to `/mcp` and edit the same project by its `project_id` → undo through MCP.
- **Tested like the rest.** The guide's HTTP and MCP examples run in `service/tests`
  against the in-memory stores, as `tests/test_docs.py` does for the agent guide.
- README, AGENTS.md and the agent guide point to the service and the two frontends.

### 12.10 Deployment and CI

- **Render.** `service/Dockerfile` (built from the repository root) installs epm with its
  plot extra and `service/requirements.txt`, runs as a non-root user, and starts
  `python -m service.service --host 0.0.0.0 --port $PORT`. `render.yaml` is the blueprint:
  one Docker web service on the `web-backend` branch, health-checked at `/api/health`. The
  retention and backup job runs daily inside the service.
- **Supabase.** Apply `service/schema.sql` once.
- **Vercel.** Root directory `frontend-web`, production branch `web-frontend`, build
  `npm run build`, output `dist`, `VITE_API_URL` set to the Render URL.
- **Branches.** `scripts/split-branches.sh` builds `local-app`, `web-frontend` and
  `web-backend` from `main`, each with only what its target needs, adding a commit to each
  branch whose files changed (never force-pushing). The *Deployment branches* workflow runs
  it after CI passes on `main`; CI itself skips those branches. `web-backend` keeps its own
  `service/config.py`, which holds the credentials; on `main` it has placeholders.
- **Smoke test.** `python -m service.smoke URL` checks a deployed service end to end
  (health, copy, edit, run, export, MCP) and deletes what it created. CI builds the Docker
  image and runs the smoke test against it.
- **Manual steps** (accounts, credentials, connecting Render and Vercel) are in
  [deployment.md](deployment.md).
- **CI.** The existing jobs, with `frontend` renamed to `frontend-local`, plus a `service`
  job (tests, Docker build, smoke test), a `frontend-web` job (typecheck, unit tests, build)
  and its Playwright end-to-end tests against the service on in-memory stores.

### 12.11 Notes from building M13–M17

- **epm changes.** Besides `workspace_snapshot` and the in-memory archive helpers, the
  in-process `fm` runner moved out of `Session` into `run_cli(ws, argv)`, so the service
  answers `show`, `list`, `validate` and `table show` with exactly the CLI's JSON.
- **Running.** `python -m service.service` (Supabase and R2 from `config.py`) or
  `--memory`. It refuses to start while `config.py` still has placeholders.
- **Uploads are raw bodies**, not multipart: `POST /api/projects/import?name=` with the
  `.epm` bytes, and `PUT /api/projects/{id}/files/{path}` with the file. A file upload is a
  `file` op, so it is validated and versioned like any other edit. Uploads are capped at
  50 MB.
- **Extra read routes** mirror the MCP tools: `GET .../objects?kind=`, `.../show/{name}`,
  `.../validate`, `.../tables/{name}`, and `GET /api/builtins`.
- **MCP** runs stateless with JSON responses on the app's own `/mcp` route (no mount, so no
  redirect to `/mcp/`). `apply_batch` defaults to `dry_run`; tool errors start with the epm
  code (`read_only_project: ...`). Tools beyond the plan: `undelete_project`, `show_table`,
  `list_builtins`.
- **Undo walks back.** A restore records `restored_from`; `undo` treats a restore as the
  version it restored, so undoing twice goes back two changes instead of undoing the undo.
- **Concurrency.** Requests for one project are serialised in-process, and `commit_version`
  guards across replicas. Writes without `expected_version` retry once on a conflict.
- **Web app.** Routes are `/`, `/p/{id}[?version=N]` and `/docs[/{guide}]`, with a small
  router instead of a routing library. It polls the project every 5 s and loads versions
  saved elsewhere, unless an editor has unsaved edits, in which case it offers to. A commit
  that loses a race shows "changed elsewhere" in the editor and the top bar. The Changes
  panel only shows previews; Versions, Files and Outputs panels replace history, proposals
  and the terminal. The docs page bundles `docs/user-guide.md` and `docs/agent-guide.md`.

### 12.12 Notes from M18

- **The user guide's examples are real.** Its `bash` blocks that use `$EPM` run in order as
  one script, through `bash`, `curl` (failing on any HTTP error) and `jq`, against a live
  service on in-memory storage; its Python MCP example then runs against the project that
  script created. Every HTTP route and MCP tool must appear in the guide.
- **One guide for people and agents on the service.** Model conventions stay in the agent
  guide; the user guide covers projects, versions and recovery, files, sharing, access, and
  the HTTP and MCP references. The web app's docs page shows both, with links between them
  rewritten to its own pages.

## 13. Risks

- **Recurrence speed.** Period stepping is slower than vectorized evaluation. Mitigated by
  scalar point evaluation (see the M12 notes), and models have few periods (about 10–40).
- **Sandbox limits.** `setrlimit` is Unix-only, and Python-level sandboxing is not a security
  boundary. Treat `functions.py` as trusted-by-the-user code with resource limits, not as an
  untrusted-code sandbox. Document this clearly.
- **Solver convergence.** Use damping and a clear failure status, and fall back to
  opening-balance interest if needed.
- **Open alpha.** Anyone who finds the service URL can read, edit and delete any project
  except the example, and run custom functions on the server. Versions, soft delete and
  backups make damage recoverable; Supabase Auth closes it. Don't put real deal data in the
  alpha.
- **Hard-coded credentials.** Anyone with access to the repository has the Supabase and R2
  keys. Rotate them, and move them to environment variables, before the repository or the
  app is shared further.
- **Per-request cost.** Stateless requests rebuild the engine and start the function worker
  each time. The blob and value caches keep this to a fraction of a second for the sample
  LBO; if it isn't enough, keep recently used workspaces in memory keyed by
  `(project_id, version)`, which stays stateless in behaviour.
