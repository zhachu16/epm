# epm service (web backend)

The hosted epm service: the epm engine behind an **HTTP API** (`/api`) and an **MCP
endpoint** (`/mcp`), with project records in a local **SQLite** file and the files (model
archives, inputs and outputs) in **Cloudflare R2**. Every saved change is a new version of a
project, and any version can be restored. The web app on the `web-frontend` branch and MCP
clients such as Claude both work on the same projects.

> This branch is generated from `main` of [zhachu16/epm](https://github.com/zhachu16/epm)
> after its checks pass. Make changes on `main`; anything committed here is overwritten.
> Nothing secret is kept in the repository: credentials are environment variables on the
> server that runs the service.

| Path | What |
| --- | --- |
| [`service/`](service) | The service: `service.py` (entry point), `projects.py` (projects and versions), `api.py` (HTTP), `tools.py` (MCP), `auth.py` (the access token), `store/` (SQLite, R2, in-memory), `migrate.py` (load records from a backup or from Supabase), `Dockerfile`, `smoke.py` |
| [`epm/`](epm) | The engine the service runs |
| [`examples/lbo/`](examples/lbo) | The sample LBO, served read-only as the `example-lbo` project |
| [`docs/`](docs) | The [user guide](docs/user-guide.md) (HTTP and MCP reference), [deployment](docs/deployment.md), the [agent guide](docs/agent-guide.md) |

## Run it

With in-memory storage (lost on exit), for trying it out:

```text
pip install ".[plot]" -r service/requirements.txt
python -m service.service --memory
```

The API is then at `http://127.0.0.1:8000/api` (interactive docs at `/api/docs`) and MCP at
`http://127.0.0.1:8000/mcp`. Without `--memory` the service keeps records in
`$EPM_DATA/epm.db` and files in R2, and refuses to start until `API_TOKEN` and the three
`R2_*` variables are set (see [`service/config.py`](service/config.py)).

With Docker:

```text
docker build -f service/Dockerfile -t epm-service .
docker run -p 10000:10000 epm-service \
  sh -c 'exec python -m service.service --memory --host 0.0.0.0 --port 10000'
```

## Deploy on a server

1. Create an R2 bucket and a token for it.
2. Put `API_TOKEN` and the R2 credentials in a `.env` file on the server.
3. Run the image with that file and a directory for the database, behind a reverse proxy
   that terminates TLS:

```text
docker run -d --name epm --restart unless-stopped --env-file .env \
  -v /srv/epm/data:/data -p 127.0.0.1:10000:10000 epm-service
```

4. Check the deployed service end to end:

```text
python -m service.smoke https://epm.example.com/<token>
```

[deployment.md](docs/deployment.md) has every step, including backups, moving the records
over from Supabase, and Vercel for the web app.

## Use it

- **HTTP:** projects at `/api/projects`, each with state, batches of edits, runs, sweeps,
  analyses, files, outputs and versions. See the [user guide](docs/user-guide.md#http-api-reference).
- **MCP:** connect a client to `/mcp` (streamable HTTP). Tools take a `project_id`; agents
  preview edits with `apply_batch`, and `undo` or `rollback` recover. See the
  [user guide](docs/user-guide.md#mcp-reference).
- **Access:** a deployed service answers only at URLs that contain its token:
  `https://<host>/<token>/api/...` and `https://<host>/mcp/<token>`. There are no accounts;
  whoever holds the token can read and change every project.

## Test it

```text
pip install -e ".[dev]" -r service/requirements.txt
pytest -q service/tests
```

The tests use in-memory storage and include running every example in the user guide
against a live service.
