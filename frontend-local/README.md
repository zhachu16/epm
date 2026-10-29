# epm frontend

The web UI for `fm serve`: a searchable model explorer, the dependency graph, editors for
every kind of model object, analyses with charts, and a bottom panel for change previews,
problems, an `fm` terminal and logs.

It's a standalone Vite + React + TypeScript app. `fm serve` serves the built files from
`frontend-local/dist`; set `EPM_FRONTEND` or pass `--frontend` to serve them from elsewhere.

```bash
npm ci
```

```bash
npm run build
```

Then serve a model from the repository root:

```bash
fm -m examples/lbo serve --open
```

## Development

Run the API without a token, then start Vite. It proxies `/api` to port 8765:

```bash
fm -m examples/lbo serve --no-auth
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
starts `fm serve` on a scratch copy of the sample LBO using `python3`, or the interpreter in
`EPM_PYTHON`, which needs epm installed. Install a browser once with
`npx playwright install chromium`.
