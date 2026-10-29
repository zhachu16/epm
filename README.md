# epm service (web backend)

The hosted epm service: the epm engine behind an **HTTP API** (`/api`) and an **MCP
endpoint** (`/mcp`), with projects stored in **Supabase** (metadata) and **Cloudflare R2**
(model archives, input files and outputs). Every saved change is a new version of a
project, and any version can be restored. The web app on the `web-frontend` branch and MCP
clients such as Claude both work on the same projects.

> This branch is generated from `main` of [zhachu16/epm](https://github.com/zhachu16/epm)
> after its checks pass, and **Render deploys it**. Make changes on `main`; anything
> committed here is overwritten, except `service/config.py`, which holds this deployment's
> credentials and is kept.

| Path | What |
| --- | --- |
| [`service/`](service) | The service: `service.py` (entry point), `projects.py` (projects and versions), `api.py` (HTTP), `tools.py` (MCP), `store/` (Supabase, R2, in-memory), `schema.sql`, `Dockerfile`, `smoke.py` |
| [`epm/`](epm) | The engine the service runs |
| [`examples/lbo/`](examples/lbo) | The sample LBO, served read-only as the `example-lbo` project |
| [`render.yaml`](render.yaml) | The Render blueprint |
| [`docs/`](docs) | The [user guide](docs/user-guide.md) (HTTP and MCP reference), [deployment](docs/deployment.md), the [agent guide](docs/agent-guide.md) |

## Run it

With in-memory storage (lost on exit), for trying it out:

```text
pip install ".[plot]" -r service/requirements.txt
python -m service.service --memory
```

The API is then at `http://127.0.0.1:8000/api` (interactive docs at `/api/docs`) and MCP at
`http://127.0.0.1:8000/mcp`. Without `--memory` the service uses Supabase and R2 as set in
`service/config.py`, and refuses to start while those settings are placeholders.

With Docker, as Render runs it:

```text
docker build -f service/Dockerfile -t epm-service .
docker run -p 10000:10000 epm-service \
  sh -c 'exec python -m service.service --memory --host 0.0.0.0 --port 10000'
```

Without the command at the end, the container starts the service on Supabase and R2.

## Deploy on Render

1. Run [`service/schema.sql`](service/schema.sql) once in Supabase, create an R2 bucket and
   token, and fill in [`service/config.py`](service/config.py) **on this branch**. It is the
   one file here that is yours to edit: updates from `main` keep it as it is.
2. In Render, **New → Blueprint** on this repository and branch: `render.yaml` defines one
   Docker web service built from `service/Dockerfile`, health-checked at `/api/health`.
3. Check the deployed service end to end:

```text
python -m service.smoke https://epm-service.onrender.com
```

[deployment.md](docs/deployment.md) has every step, including Vercel for the web app.

## Use it

- **HTTP:** projects at `/api/projects`, each with state, batches of edits, runs, sweeps,
  analyses, files, outputs and versions. See the [user guide](docs/user-guide.md#http-api-reference).
- **MCP:** connect a client to `/mcp` (streamable HTTP). Tools take a `project_id`; agents
  preview edits with `apply_batch`, and `undo` or `rollback` recover. See the
  [user guide](docs/user-guide.md#mcp-reference).
- **Access:** the alpha is open, with no sign-in or API keys. Don't store confidential
  models in it.

## Test it

```text
pip install -e ".[dev]" -r service/requirements.txt
pytest -q service/tests
```

The tests use in-memory storage and include running every example in the user guide
against a live service.
