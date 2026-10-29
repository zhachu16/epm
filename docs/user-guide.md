# User guide

epm builds financial models from **named metrics and formulas** instead of cell
references. This guide covers the hosted version: the web app, the projects it holds, and
the two ways programs and agents reach the same projects, the **HTTP API** and **MCP**.

- **The web app** lists your projects and opens each one in an editor: an explorer, the
  dependency graph, editors for every kind of object, analyses with charts, and panels for
  versions, files and outputs.
- **The HTTP API** under `/api` does everything the web app does. The examples below use
  `curl` and `jq`.
- **MCP** at `/mcp` gives agents the same projects as tools. An agent and a person can work
  on one project at the same time.

The [agent guide](agent-guide.md) is the reference for the model itself: definitions,
formulas, operations and diagnostics.

> **Alpha.** The app is open: there is no sign-in and anyone with the address can read and
> change any project except the example. Don't put confidential data in it. Sign-in
> (Supabase Auth) comes later; see [Access](#access).

## Getting started

This walk-through copies the example LBO, changes an assumption, runs the model, saves and
exports the results, then edits the same project from an agent over MCP. Each step shows
the web app and the same thing over HTTP.

Set `EPM` to the service's address first; the commands below use it:

```bash
export EPM=https://epm-service.example.com
```

### 1. Open the app

Open the web app's address. During the alpha there is nothing to sign in to: you land on
**Projects**, which lists the example and your projects. Over HTTP:

```bash
curl -s "$EPM/api/health"
curl -s "$EPM/api/projects" | jq '.[] | {id, name, current_version}'
```

### 2. Copy the example project

The example, **Example: LBO** (`example-lbo`), is read-only. Click **Make an editable copy**
and name the copy. The app opens it; its address, `/p/<project_id>`, is the project's link.

```bash
PROJECT=$(curl -s -X POST "$EPM/api/projects" -H 'Content-Type: application/json' \
  -d '{"name": "Atlas LBO", "from": "example-lbo"}' | jq -r .id)
echo "$PROJECT"
```

Every project has a **project ID** like `p_7f3k9q2mzx`. The HTTP API takes it in the path,
and every MCP tool that works on a project takes it as `project_id`.

### 3. Change an assumption

In the explorer, open **Assumptions → Financing → interest_rate**, set the value to `0.05`
and click **Preview**. The **Changes** panel shows the definition diff and how the key
outputs move. Click **Save**: the top bar shows **Saved · v2**.

Over HTTP, a change is a batch of operations. Preview it with `"mode": "dry_run"`:

```bash
curl -s -X POST "$EPM/api/projects/$PROJECT/batches" -H 'Content-Type: application/json' \
  -d '{"mode": "dry_run", "ops": [
        {"op": "update", "kind": "metric", "name": "interest_rate", "set": {"value": 0.05}}]}' \
  | jq '.impact.outputs.equity_irr'
```

```json
{"before": {"axis": null, "value": 0.1735}, "after": {"axis": null, "value": 0.1697}, "status": "ok", "delta": -0.0038}
```

Raising the base rate from 4% to 5% lowers the equity IRR from 17.3% to 17.0%. Then save
it. `expected_version` makes the save fail, instead of overwriting, if someone
saved a newer version in the meantime:

```bash
curl -s -X POST "$EPM/api/projects/$PROJECT/batches" -H 'Content-Type: application/json' \
  -d '{"expected_version": 1, "label": "Base rate up 100bp", "ops": [
        {"op": "update", "kind": "metric", "name": "interest_rate", "set": {"value": 0.05}}]}' \
  | jq '{committed, version}'
```

```json
{"committed": true, "version": 2}
```

### 4. Run the model

The web app recalculates as you save: select `equity_irr` in the graph or explorer to see
it in the **Inspector**. A run evaluates the model, optionally with temporary overrides
that are never saved:

```bash
curl -s -X POST "$EPM/api/projects/$PROJECT/run" -H 'Content-Type: application/json' \
  -d '{"outputs": ["equity_irr", "moic"]}' | jq .values
curl -s -X POST "$EPM/api/projects/$PROJECT/run" -H 'Content-Type: application/json' \
  -d '{"overrides": {"exit_multiple": 8}, "outputs": ["equity_irr"]}' | jq .values.equity_irr.value
```

### 5. Inspect and export the results

Open **Tables → returns** to see the returns by period, or **Analyses → entry_exit_irr**
and click **Run** for the entry × exit multiple heatmap. **Save results** keeps the
results (CSV) and the chart (PNG) as **outputs** of the project; the **Outputs** panel
lists them for download. **Export .epm** in the top bar downloads the whole project.

```bash
curl -s "$EPM/api/projects/$PROJECT/tables/returns" | jq -c '[.rows[].metric | select(.)]'
curl -s -X POST "$EPM/api/projects/$PROJECT/analyses/entry_exit_irr/run" \
  -H 'Content-Type: application/json' -d '{"save": true, "plot": true}' \
  | jq '.outputs[] | {id, kind, name}'
```

Download the chart, the results and the project:

```bash
PLOT=$(curl -s "$EPM/api/projects/$PROJECT/outputs" | jq -r 'map(select(.kind == "plot"))[0].id')
curl -s -o entry_exit_irr.png "$EPM/api/projects/$PROJECT/outputs/$PLOT"
curl -s -o atlas.epm "$EPM/api/projects/$PROJECT/export"
```

### 6. Edit the same project through MCP

Connect an MCP client to `$EPM/mcp` (streamable HTTP; no credentials during the alpha).
For Claude Code:

```text
claude mcp add --transport http epm https://epm-service.example.com/mcp
```

Then ask the agent, for example: *"In project p_7f3k9q2mzx, raise the tax rate to 30%,
show me the effect on equity IRR first, then save it."* The agent previews with
`apply_batch` (`mode: "dry_run"`, the default), then commits with `mode: "commit"`. The
web app picks up the agent's version within a few seconds. *"Undo that"* makes the agent
call `undo`.

The same steps with the Python MCP SDK:

```python
import asyncio
import os

from mcp import Client

EPM = os.environ["EPM"]  # the service's address
PROJECT = os.environ["PROJECT"]  # the project ID from step 2
TAX = [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}]


async def main():
    async with Client(f"{EPM}/mcp") as client:
        shown = await client.call_tool("show", {"project_id": PROJECT, "name": "tax_rate"})
        print("tax rate:", shown.structured_content["value"])

        preview = await client.call_tool("apply_batch", {"project_id": PROJECT, "ops": TAX})
        print("IRR after:", preview.structured_content["impact"]["outputs"]["equity_irr"]["after"])

        saved = await client.call_tool(
            "apply_batch", {"project_id": PROJECT, "ops": TAX, "mode": "commit"}
        )
        print("saved version", saved.structured_content["version"])

        undone = await client.call_tool("undo", {"project_id": PROJECT})
        print("back to version", undone.structured_content["restored"])


asyncio.run(main())
```

## Building models

The [agent guide](agent-guide.md) has the details; the conventions in short:

- **Name every quantity once.** A metric is a value, values per period, a value read from
  an input file, or a formula over other metrics' names, such as
  `ebitda - depreciation_amortization`. Never bury a number in a formula: create an
  assumption metric for it, so sensitivities can reach it.
- **Give metrics a unit and a folder.** Units (`USD m`, `%`, `x`) are checked when values
  are added or compared; folders organise the explorer.
- **Use `prev()` for anything that rolls forward**, such as balances: `prev(cash,
  init=minimum_cash) + fcf`. A same-period loop (interest on average debt) must be declared
  as a circularity, which is solved by iteration.
- **Keep checks passing.** Checks such as "the balance sheet balances" are evaluated on
  every run and shown in the top bar.
- **Preview before saving.** Every editor has **Preview**, which shows the change and its
  effect on the key outputs without saving anything.

## Projects

A project is one model: its definitions, its input files and its custom functions, plus
its saved versions and outputs.

| Action | Web app | HTTP |
| --- | --- | --- |
| List and search | **Projects** page, search box | `GET /api/projects?q=atlas` |
| Create an empty model | **New project** | `POST /api/projects` with `{"name": ...}` |
| Copy a project | **Duplicate**, or **Make an editable copy** for the example | `POST /api/projects/{project_id}/duplicate` |
| Open | click its name, or go to `/p/{project_id}` | `GET /api/projects/{project_id}/state` |
| Rename | click the name in the top bar, or **Rename** | `PATCH /api/projects/{project_id}` with `{"name": ...}` |
| Delete | **Delete** | `DELETE /api/projects/{project_id}` |
| Bring back | **Show recently deleted → Restore** | `POST /api/projects/{project_id}/restore` |

- **Project IDs** are assigned at creation and never change, even when the project is
  renamed. Use them in links, API calls and MCP tools.
- **Names.** A project's name is also its model's own name (`settings.name`), the one an
  exported `.epm` file carries. Renaming saves a new version; changing `settings.name`
  renames the project; restoring a version brings back the name it had.
- **The example** (`example-lbo`) is read-only for everyone. You can open it, preview
  changes, run it and download it; copy it to change it.
- **Deleting** hides a project. It can be restored for 30 days, then it is removed for
  good.

```bash
curl -s "$EPM/api/projects?q=atlas" | jq '.[] | {id, name}'
curl -s -X PATCH "$EPM/api/projects/$PROJECT" -H 'Content-Type: application/json' \
  -d '{"description": "Project Atlas, base case"}' | jq .description
```

## Saving, versions and recovery

**There is no save button for the project.** Every saved change creates a new numbered
**version** straight away, from the web app, the API or an agent.

- **Editors** save when you click **Save**. `model.json` and `functions.py` also save
  themselves after a pause in typing (turn off **autosave** in the editor's bar to stop
  that). A change the model rejects is not saved; the editor shows why.
- **The save status** in the top bar shows **Saved · v7** when everything is saved,
  **Unsaved edits** while an editor holds changes you haven't saved, and **Not saved:
  changed elsewhere** when a save lost a race (below). The browser warns before you leave
  with unsaved edits.
- **Versions panel.** Lists every version with what changed, when and from where (web app,
  API or agent). **View** opens a version read-only; **.epm** downloads it.
- **Restore** saves an old version again as the newest version, so history is never
  rewritten and a restore can itself be undone. **Undo last change** restores the version
  before the current one; undoing again keeps going back.
- **Retention.** The newest 50 versions are kept, plus the last version of each day for 90
  days. Project data is backed up daily.

```bash
curl -s "$EPM/api/projects/$PROJECT/versions" | jq -c '.[] | {number, label, source}'
curl -s -X POST "$EPM/api/projects/$PROJECT/undo" | jq .version.number
curl -s -X POST "$EPM/api/projects/$PROJECT/versions/2/restore" | jq .version.number
curl -s -o atlas-v1.epm "$EPM/api/projects/$PROJECT/versions/1/export"
```

**Working with others, and with agents.** Two people, or a person and an agent, can work on
one project. The web app checks for newer versions every few seconds and loads them,
unless you have unsaved edits; then it offers to. A save sends the version it was based on
(`expected_version`), so it never overwrites someone else's newer version. If it would, it
is refused with `409`:

```json
{"ok": false, "errors": [{"code": "version_conflict", "message": "the project was saved since version 1 (it is now at version 2); reload and try again", "current_version": 2}]}
```

Reload, re-apply your edit, and save again. A write without `expected_version` (typical for
agents) applies to whatever version is current.

## Files

**Import and export.** An `.epm` file is the whole model: `model.json`, the input files and
`functions.py`, zipped. **Export .epm** downloads it; **Import .epm…** on the Projects page
creates a new project from one. The same file opens in the local app (`fm serve`).

```bash
curl -s -X POST "$EPM/api/projects/import?name=Atlas%20imported" --data-binary @atlas.epm \
  | jq '{id, name}'
```

**Input files.** Source files the model reads (CSV, XLSX) live under `inputs/`. The **Files**
panel lists them for download; **Upload…** adds or replaces one, and **Delete** removes one
that nothing uses. Each of these saves a new version. Then map the file with a source
(**+ New → Source**).

```bash
printf 'item,2025,2026\ncapex_budget,40,45\n' > budget.csv
curl -s -X PUT "$EPM/api/projects/$PROJECT/files/budget.csv" --data-binary @budget.csv \
  | jq '{committed, version}'
curl -s "$EPM/api/projects/$PROJECT/files" | jq -r '.[].name'
curl -s -o historicals.xlsx "$EPM/api/projects/$PROJECT/files/inputs/historicals.xlsx"
```

**Outputs** are results you chose to keep: a run, a sweep, an analysis's results (CSV) and
its chart (PNG). Each records the version it came from. Runs and sweeps are kept with
`"save": true`:

```bash
curl -s -X POST "$EPM/api/projects/$PROJECT/sweep" -H 'Content-Type: application/json' \
  -d '{"inputs": {"exit_multiple": [8, 9, 10]}, "outputs": ["equity_irr", "moic"], "save": true}' \
  | jq '.output | {id, name}'
curl -s "$EPM/api/projects/$PROJECT/outputs" | jq -c '.[] | {kind, name, version}'
```

A chart can also be downloaded without saving it:
`GET /api/projects/{project_id}/analyses/{name}/plot.png`.

## Sharing links

A project's link is the web app's address plus `/p/{project_id}`; **Copy link** in the top
bar copies it. During the alpha anyone with the link can open and edit the project. Once
sign-in arrives, a recipient will sign in first and then land on the project. A link to a
version, `/p/{project_id}?version=3`, opens that version read-only.

## Access

During the alpha there is **no authentication**: the web app, the HTTP API and MCP are open
to anyone who knows their address, and there are no API keys or tokens to configure. The
only protection is that the example project can't be changed. Sign-in with Supabase Auth is
planned; when it arrives the web app will ask you to sign in, and API and MCP clients will
send a session token. Project IDs, links and the API stay the same.

## HTTP API reference

JSON in and out, except uploads (the request body is the file) and downloads. Errors are
`{"ok": false, "errors": [{"code", "message", ...}]}` with status 404 for unknown
projects, versions, files and outputs, 403 for writes to the example (`read_only_project`),
409 for `version_conflict`, 413 for uploads over 50 MB, and 400 otherwise. The service also
serves interactive documentation at `/api/docs`.

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | Is the service up |
| `GET /api/builtins` | Formula functions with signatures |
| `GET /api/projects?q=&deleted=` | List or search projects; `deleted=true` lists deleted ones |
| `POST /api/projects` | Create: `{"name", "description", "from"}`; `from` copies a project |
| `POST /api/projects/import?name=` | Create from an `.epm` file (the request body) |
| `GET /api/projects/{project_id}` | A project's name, description and current version |
| `PATCH /api/projects/{project_id}` | Rename (saves a version), or change the description |
| `DELETE /api/projects/{project_id}` | Delete (restorable for 30 days) |
| `POST /api/projects/{project_id}/duplicate` | Copy into a new project: `{"name"}` |
| `POST /api/projects/{project_id}/restore` | Bring back a deleted project |
| `GET /api/projects/{project_id}/export?version=` | Download as `.epm` |
| `GET /api/projects/{project_id}/state?version=` | Everything the web app shows: definitions, values, checks, diagnostics |
| `GET /api/projects/{project_id}/objects?kind=&search=&folder=` | List metrics, inputs, tables, analyses, ... |
| `GET /api/projects/{project_id}/show/{name}` | One object: definition, value, dependencies |
| `GET /api/projects/{project_id}/trace/{name}?direction=&depth=` | Upstream or downstream dependencies |
| `GET /api/projects/{project_id}/validate` | Diagnostics and check results |
| `GET /api/projects/{project_id}/tables/{name}` | A table's rows and values |
| `GET /api/projects/{project_id}/sources/{name}/preview` | What a source file offers to bind to |
| `POST /api/projects/{project_id}/batches` | Apply operations: `{"ops", "mode", "expected_version", "label"}` |
| `PUT /api/projects/{project_id}/model` | Replace `model.json`: `{"model", "mode", "expected_version"}` |
| `PUT /api/projects/{project_id}/functions` | Replace `functions.py`: `{"code", "mode", "expected_version"}` |
| `POST /api/projects/{project_id}/run` | Evaluate: `{"overrides", "outputs", "save"}` |
| `POST /api/projects/{project_id}/sweep` | Scenarios: `{"inputs", "outputs", "mode", "overrides", "save"}` |
| `POST /api/projects/{project_id}/analyses/{name}/run` | Run a saved analysis: `{"save", "plot"}` |
| `GET /api/projects/{project_id}/analyses/{name}/plot.png` | An analysis chart |
| `GET /api/projects/{project_id}/files` | Input files |
| `GET /api/projects/{project_id}/files/{path}` | Download an input file |
| `PUT /api/projects/{project_id}/files/{path}?expected_version=` | Add or replace an input file (the request body) |
| `DELETE /api/projects/{project_id}/files/{path}` | Remove an input file |
| `GET /api/projects/{project_id}/outputs` | Saved outputs |
| `GET /api/projects/{project_id}/outputs/{output_id}` | Download an output |
| `DELETE /api/projects/{project_id}/outputs/{output_id}` | Delete an output |
| `GET /api/projects/{project_id}/versions` | Versions, newest first |
| `GET /api/projects/{project_id}/versions/{number}/export` | Download a version as `.epm` |
| `POST /api/projects/{project_id}/versions/{number}/restore` | Save that version again as the newest |
| `POST /api/projects/{project_id}/undo` | Go back one change |

Batches, definitions and formulas are described in the [agent guide](agent-guide.md#changing-a-model).

## MCP reference

The endpoint is `/mcp` on the service, over streamable HTTP, with no credentials during the
alpha. Every tool that works on an existing project requires `project_id`; only
`list_projects`, `search_projects`, `create_project` and `list_builtins` don't. Errors come
back as tool errors whose text starts with the error code, for example
`read_only_project: 'Example: LBO' is the read-only example; ...`.

| Tool | Does |
| --- | --- |
| `list_projects`, `search_projects` | Find projects and their IDs |
| `create_project` | A new empty model, or a copy (`from_project: "example-lbo"`) |
| `get_project`, `rename_project` | Read a project, with `web_url`, its page in the web app; rename it or change its description |
| `duplicate_project`, `delete_project`, `undelete_project` | Copy, delete, bring back |
| `list_objects`, `show`, `trace`, `show_table`, `validate` | Read the model |
| `list_builtins` | The formula functions |
| `apply_batch` | Change the model: `mode: "dry_run"` (the default) previews, `"commit"` saves a version |
| `run`, `sweep`, `run_analysis` | Evaluate; `save: true` keeps the result as an output |
| `list_files`, `list_outputs`, `export_project` | Files; outputs and exports come with download URLs |
| `list_versions`, `undo`, `rollback` | History and recovery: `rollback` restores any version |

Ask an agent to undo or roll back, and it uses `undo` or `rollback`; like the web app's
restore, both save a new version, so nothing is lost.

The repository's `skills/` folder has two Agent Skills, `epm-create-model` and
`epm-edit-model`, that teach an agent this workflow: read before writing, preview with
`dry_run`, commit with `expected_version`, validate, and recover with `undo`.

## Running the service yourself

The service is the `service/` folder of the repository, on top of the `epm` package. With
in-memory storage, for trying it out:

```text
pip install ".[plot]" -r service/requirements.txt
python -m service.service --memory
```

The web app (`frontend-web/`) then runs with `npm run dev` and talks to it; see its README.
For real storage and hosting on Render and Vercel, see [deploying epm](deployment.md).
