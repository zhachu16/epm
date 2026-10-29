# Working on epm

Notes for agents and people changing this repository. To *use* epm to build models, read
[`docs/agent-guide.md`](docs/agent-guide.md); the design is in [`docs/PLAN.md`](docs/PLAN.md).

## Layout

| Path | What |
| --- | --- |
| `epm/formula/` | Formula language: parser (Python `ast` → IR), shape and unit inference, built-ins, the whole-series compiler (`compiler.py`) and the period-by-period one for recurrences (`point.py`). |
| `epm/engine/` | Planner (dependency components → steps), evaluation, fingerprints and the value cache. |
| `epm/ops/` | Edit operations, atomic batches, renames, previews. Every change to a model goes through here. |
| `epm/workspace.py` | One open model: engine, cache, undo history, pending proposals. |
| `epm/functions/` | Custom functions: static discovery, and the sandboxed worker process. |
| `epm/sources/`, `epm/package/`, `epm/analysis/` | CSV/XLSX mapping, `.epm` archives, sweeps and analyses. |
| `epm/cli/`, `epm/server/` | `fm` (Typer) and `fm serve` (FastAPI); the CLI forwards to a running server. |
| `frontend-local/` | The local app's UI, served by `fm serve`. A standalone Vite + React app with its own `package.json`. |
| `service/` | The cloud service: projects in Supabase and R2, an HTTP API and an MCP endpoint over `epm`. `service/service.py` runs it. |
| `frontend-web/` | The cloud web app, hosted on Vercel. A standalone Vite + React app, separate from `frontend-local`. |
| `examples/lbo/` | The sample LBO, checked by golden tests against an independent calculation. |
| `benchmarks/run.py` | Timings for runs, edits and 1k-scenario sweeps. |
| `skills/` | Agent Skills for working on models through the hosted service's MCP connector: `epm-create-model` and `epm-edit-model`. |
| `scripts/split-branches.sh`, `render.yaml` | Deployment: the generated `local-app`, `web-frontend` and `web-backend` branches, and the Render blueprint. See [`docs/deployment.md`](docs/deployment.md). |

## Checks

```bash
pip install -e ".[dev]"
```

```bash
ruff check . && ruff format --check . && pytest -q
```

```bash
cd frontend-local && npm ci && npm run typecheck && npm test && npm run build
```

```bash
cd frontend-local && EPM_PYTHON=$(which python) npm run e2e
```

```bash
pip install -r service/requirements.txt && pytest -q service/tests
```

```bash
cd frontend-web && npm ci && npm run typecheck && npm test && npm run build
```

```bash
cd frontend-web && EPM_PYTHON=$(which python) npm run e2e
```

CI runs all of these. The end-to-end tests need the built UI and a Chromium for Playwright
(`npx playwright install chromium`).

## Conventions

- **Model problems are diagnostics, not exceptions.** Building an engine never raises for a
  bad model; problems are collected with a code, a message and the object they concern, and
  dependants report `blocked`. Raise `EpmError` only for bad requests (unknown names, bad
  arguments).
- **One way to change a model.** The UI, the CLI and agents all apply `Op` batches through
  `Workspace.apply`. Don't add another path that edits `model.json`.
- **Two evaluators, one meaning.** A formula must give identical results evaluated over the
  whole series (`compile_formula`) and period by period (`compile_point`).
  `tests/test_point.py` compares them on awkward values; add new built-ins to it. A built-in
  without a scalar form falls back to whole-series evaluation automatically.
- **Cache keys.** Step fingerprints cover definitions, input files, functions and upstream
  fingerprints. If a change alters results for unchanged definitions, bump
  `ENGINE_VERSION` in `epm/engine/engine.py` so stale caches are ignored.
- **Docs are tested.** `tests/test_docs.py` applies every batch, runs every `fm -m
  examples/lbo …` command and every Python example in `README.md` and the agent guide, and
  checks that every built-in and listed diagnostic code is documented. A new built-in needs a
  row in the guide's formula table. `service/tests/test_docs.py` runs every `curl` and
  Python example in `docs/user-guide.md` against a live in-memory service, and checks that
  every HTTP route and MCP tool is listed there. `service/tests/test_skills.py` builds the
  skills' example models and checks every tool and argument they name, so a new or renamed
  MCP tool needs the skills' `reference.md` updated (both copies).
- **API shapes.** The frontends' types live in `frontend-local/src/api/types.ts` and
  `frontend-web/src/api/types.ts`; keep them in step with what `epm/server/app.py` and
  `service/api.py` return. Both frontends read the snapshot from `workspace_snapshot()`.
- **The service is stateless.** Each request loads a project version into a fresh
  `Workspace`; a commit saves a new version. Keep per-request state out of `Projects`
  except caches that are safe to lose (version archives, calculated values).
- **Change `main`, not the deployment branches.** `local-app`, `web-frontend` and
  `web-backend` are rebuilt from `main` after CI passes; edits made on them are overwritten.
  Their READMEs are `scripts/branch-readmes/<branch>.md`; the local-app one is tested like
  `README.md`. The exception is `service/config.py` on `web-backend`: it holds the
  deployment's credentials, is edited there only, and is kept when the branch is rebuilt.
  Never put real credentials on `main`.
- **Style.** Ruff with a line length of 100. Comments explain why, not what.
