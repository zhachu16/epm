# Deploying epm

The hosted epm is two deployments fed by two branches of this repository:

| Branch | What it holds | Deployed by |
| --- | --- | --- |
| `main` | Everything. All changes are made here. | — |
| `web-backend` | `epm/`, `service/`, `examples/`, docs | You, with Docker on a server |
| `web-frontend` | `frontend-web/`, docs | Vercel |
| `local-app` | `epm/`, `frontend-local/`, `examples/`, `tests/`, `benchmarks/`, docs | Nobody: it is the local app (`fm`, `fm serve`) on its own |

The three branches are generated from `main` by `scripts/split-branches.sh`. The
**Deployment branches** workflow runs it after CI passes on `main`, so a push to `main`
reaches the branches only when every check is green. Never edit those branches: the next
run adds a commit that matches `main` again. To regenerate them by hand, run the workflow
from the Actions tab, or:

```text
scripts/split-branches.sh main
```

## What is stored where

| What | Where |
| --- | --- |
| Model archives (every version), input files, outputs | A Cloudflare R2 bucket |
| Project, version and output records | A SQLite file, `epm.db`, in the service's data directory on the server |
| Credentials and the access token | Environment variables on the server; nothing secret is in the repository |

The files are safe in R2 whatever happens to the server. The records say which file is
which project and version, so the SQLite file needs a backup of its own: see
[Backups](#backups-and-restoring).

## 1. Cloudflare R2 (files)

1. Create a bucket, `epm` (or another name; see step 2).
2. Turn on **object versioning** for the bucket if your plan offers it: a second line of
   defence behind the service's own versions.
3. In **R2 → Manage API tokens**, create a token with **Object Read & Write** on the bucket.
   Copy the **account ID**, the **access key ID** and the **secret access key**.

## 2. Settings

The service reads its settings from environment variables (`service/config.py`):

| Variable | What |
| --- | --- |
| `API_TOKEN` | The secret in the service's URLs: at least 32 URL-safe characters. Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | The R2 token from step 1 |
| `R2_BUCKET` | The bucket's name, if it is not `epm` |
| `EPM_DATA` | The directory of `epm.db`. The Docker image sets it to `/data` |

Keep them in a file that is not committed, such as `.env`:

```text
API_TOKEN=<token>
R2_ACCOUNT_ID=<account id>
R2_ACCESS_KEY_ID=<access key id>
R2_SECRET_ACCESS_KEY=<secret access key>
```

The service refuses to start without them. Everything that isn't secret (the web app's
address, CORS, retention) is in `service/deployment.py`, which changes on `main` like any
other code.

## 3. Run the service

On a server with Docker, from a checkout of the `web-backend` branch:

```text
docker build -f service/Dockerfile -t epm-service .
```

```text
docker run -d --name epm --restart unless-stopped --env-file .env \
  -v /srv/epm/data:/data -p 127.0.0.1:10000:10000 epm-service
```

- `/data` holds `epm.db`. Mount a host directory (owned by user ID 10001, which the
  container runs as) or a Docker volume there, or the records are lost with the container.
- Run **one** container per data directory: the database has a single writer.
- Put a reverse proxy that terminates TLS in front of port 10000. With
  [Caddy](https://caddyserver.com), a `Caddyfile` of three lines does it; leave request
  logging off, because the URLs contain the token:

```text
epm.example.com {
	reverse_proxy 127.0.0.1:10000
}
```

Check it end to end from a checkout of the repository:

```text
pip install ".[plot]" -r service/requirements.txt
python -m service.smoke https://epm.example.com/<token>
```

The smoke test checks health, copies the example, saves an edit, runs the model, exports
it, reads it back over MCP, and deletes the copy.

## 4. Access

The service answers only at URLs that contain the token; every other path is an empty
`404`:

| Use | URL |
| --- | --- |
| The HTTP API | `https://epm.example.com/<token>/api/...` |
| MCP | `https://epm.example.com/mcp/<token>` (also `https://epm.example.com/<token>/mcp`) |

Anything that holds one of these URLs holds the token: treat MCP client configuration and
the web app's build settings as secrets. To replace the token, change `API_TOKEN`, restart
the container and update every client.

## 5. Vercel (the web app)

1. In Vercel, **Add New → Project** and import the repository.
2. Set **Root Directory** to `frontend-web`. The framework (Vite), build command and output
   directory come from `frontend-web/vercel.json`.
3. Under **Environment Variables**, set `VITE_API_URL` to `https://epm.example.com/<token>`,
   with no trailing slash. It is read at build time, so redeploy after changing it.
4. Under **Settings → Git**, set the **Production Branch** to **`web-frontend`**. Other
   branches get preview deployments unless you turn those off there.

The token is then part of the web app's JavaScript, so anyone who can open the web app can
read it. Keep the web app's address private, or protect the deployment in Vercel, if the
service must stay private.

## 6. Links back to the web app

`WEB_URL` in `service/deployment.py` is the web app's address
(`https://epm-alpha.nommo.cloud`), so MCP's `get_project` returns `web_url`, a link to the
project in the web app. `PUBLIC_URL` can stay empty: the service takes its own address from
each request, and adds the token to the download links it returns. Change them on `main`.

## Backups and restoring

- **The files** are in R2 under `projects/`, every version of every project.
- **The records** are backed up by the service itself every day, to
  `backups/meta/<date>.json` in the same bucket. A service with no projects of its own
  writes none, so a server that lost its database cannot replace a good backup with an
  empty one. To restore the newest one (or a given date) into an empty data directory, with
  the service stopped:

```text
docker run --rm --env-file .env -v /srv/epm/data:/data epm-service \
  python -m service.migrate --check-files backup
```

- A daily backup can be a day old. For a continuous copy, replicate `epm.db` with
  [Litestream](https://litestream.io) to a bucket; the database runs in WAL mode, which is
  what Litestream needs.
- A consistent copy by hand, while the service runs:
  `sqlite3 /srv/epm/data/epm.db ".backup 'epm-copy.db'"`.

## Moving from Supabase

Earlier versions kept the records in a Supabase project. To carry them over, once, with the
new service stopped and its data directory empty:

```text
docker run --rm --env-file .env -v /srv/epm/data:/data \
  -e SUPABASE_URL=https://<project-ref>.supabase.co \
  -e SUPABASE_SERVICE_ROLE_KEY=<service-role key> \
  epm-service python -m service.migrate --check-files supabase
```

It reads the `projects`, `versions` and `outputs` tables, writes them to `epm.db`, and
confirms that every file they refer to is in the bucket. The files stay where they are, so
use the same bucket as before. Then start the service, check your projects, and retire the
Supabase project and its keys.

## Day to day

- **Ship a change:** merge it into `main`. After CI passes, the branches move and Vercel
  deploys the web app. On the server, `git pull`, build the image again and replace the
  container.
- **Roll back the service:** check out an earlier commit of `web-backend` and rebuild. The
  records and files are not touched by a rollback.
- **Look at the records:** `sqlite3 /srv/epm/data/epm.db`. Reading while the service runs
  is safe; don't write to it by hand.
