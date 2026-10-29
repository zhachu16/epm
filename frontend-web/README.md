# epm web app

The cloud web app: a projects page (search, create, import, copy, rename, export, delete),
the model editor from the local app (explorer, dependency graph, editors, analyses,
inspector), and panels for versions, input files and saved outputs. It talks to the epm
service (`service/` in the repository root) over `/api/projects/...`.

It's a standalone Vite + React + TypeScript app, separate from `frontend-local/`, and is
hosted on Vercel. `VITE_API_URL` points it at the service; without it, it calls `/api` on
its own origin.

- **Saving.** Every saved change is a new version of the project; there is no separate save
  step. `model.json` and `functions.py` save themselves after a pause in typing. The top
  bar shows whether everything is saved.
- **Versions.** The Versions panel shows every version; view one read-only, download it,
  or restore it (which saves it again as the newest version).
- **Changes elsewhere.** The app checks for versions saved elsewhere (an agent over MCP,
  another window) every few seconds and loads them, unless an editor has unsaved edits.
- **The example** is read-only; "Make an editable copy" duplicates it.
- **Docs.** `/docs` shows the guides from `docs/`, copied in by `npm run docs` before every
  dev server start and build.

```bash
npm ci
```

```bash
npm run build
```

## Development

Run the service with in-memory storage from the repository root, then start Vite. It
proxies `/api` to port 8000 (`EPM_SERVICE` changes that):

```bash
python -m service.service --memory
```

```bash
npm run dev
```

## Checks

```bash
npm run typecheck
```

```bash
npm test
```

```bash
npm run e2e
```

`npm run e2e` runs the Playwright tests against the built app: run `npm run build` first. It
serves the app with an in-memory service using `python3`, or the interpreter in
`EPM_PYTHON`, which needs epm and `service/requirements.txt` installed. Install a browser
once with `npx playwright install chromium`.

## Deploying on Vercel

Set the project's root directory to `frontend-web` and `VITE_API_URL` to the service's
URL. `vercel.json` builds with `npm run build` and sends every route to `index.html`.
