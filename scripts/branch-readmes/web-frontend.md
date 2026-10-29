# epm web app (web frontend)

The hosted epm web app: a projects page and, for each project, the model editor (explorer,
dependency graph, editors for every kind of object, analyses with charts, inspector) with
panels for versions, input files and saved outputs. Every saved change is a new version;
earlier versions can be viewed and restored. It talks to the epm service (the
`web-backend` branch) and picks up changes agents make over MCP within seconds.

> This branch is generated from `main` of [zhachu16/epm](https://github.com/zhachu16/epm)
> after its checks pass, and **Vercel deploys it**. Make changes on `main`; anything
> committed here is overwritten.

| Path | What |
| --- | --- |
| [`frontend-web/`](frontend-web) | The app: a standalone Vite + React + TypeScript package. Its [README](frontend-web/README.md) covers development and tests. |
| [`docs/`](docs) | The guides the app's `/docs` page shows: the [user guide](docs/user-guide.md) and the [agent guide](docs/agent-guide.md) |

## Deploy on Vercel

1. Import this repository in Vercel and set the **Root Directory** to `frontend-web`.
   `frontend-web/vercel.json` sets the framework (Vite), the build and the route rewrites.
2. Set the environment variable **`VITE_API_URL`** to the service's URL, such as
   `https://epm-service.onrender.com`, with no trailing slash. It is read at build time,
   so redeploy after changing it.
3. Under **Settings → Git**, set the **Production Branch** to `web-frontend`.

The full setup, including the service on Render, is in [deployment.md](docs/deployment.md).

## Run it locally

```text
cd frontend-web
npm ci
npm run dev
```

`npm run dev` proxies `/api` to a service on `http://127.0.0.1:8000`; start one from the
`web-backend` branch with `python -m service.service --memory`. `npm run build` builds
into `frontend-web/dist`, copying the guides from `docs/` into the bundle first.

## Pages

- **`/`** lists projects: search, create, import an `.epm` file, copy the read-only
  example, rename, duplicate, export and delete (restorable for 30 days).
- **`/p/{project_id}`** opens a project; this is also its sharing link.
  `?version=N` shows an earlier version read-only.
- **`/docs`** shows the user guide and the agent guide.

The alpha is open, with no sign-in; `AuthGate` is where sign-in will go.
