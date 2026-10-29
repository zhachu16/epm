# Deploying epm

The hosted epm is two deployments fed by two branches of this repository:

| Branch | What it holds | Deployed by |
| --- | --- | --- |
| `main` | Everything. All changes are made here. | — |
| `web-backend` | `epm/`, `service/`, `examples/`, `render.yaml`, docs | Render, as a Docker web service |
| `web-frontend` | `frontend-web/`, docs | Vercel |
| `local-app` | `epm/`, `frontend-local/`, `examples/`, `tests/`, `benchmarks/`, docs | Nobody: it is the local app (`fm`, `fm serve`) on its own |

The three branches are generated from `main` by `scripts/split-branches.sh`. The
**Deployment branches** workflow runs it after CI passes on `main`, so a push to `main`
reaches Render and Vercel only when every check is green. Never edit those branches, except
`service/config.py` on `web-backend` (step 3): the next run adds a commit that matches
`main` again. To regenerate them by hand, run the
workflow from the Actions tab, or:

```text
scripts/split-branches.sh main
```

Everything below is done once, by hand, in the services' dashboards.

## 1. Supabase (project metadata)

1. Create a project at supabase.com.
2. In **SQL Editor**, run the whole of `service/schema.sql`. It creates the `projects`,
   `versions` and `outputs` tables and the functions the service calls, with row-level
   security on so the public key can read nothing.
3. In **Project Settings → API**, copy the **Project URL** and the **service_role** key.

## 2. Cloudflare R2 (files)

1. Create a bucket, `epm` (or another name; see step 3).
2. Turn on **object versioning** for the bucket if your plan offers it: a second line of
   defence behind the service's own versions.
3. In **R2 → Manage API tokens**, create a token with **Object Read & Write** on the bucket.
   Copy the **account ID**, the **access key ID** and the **secret access key**.

## 3. Fill in the service's settings

Edit `service/config.py` **on the `web-backend` branch**: `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and
`R2_BUCKET` if it is not `epm`. Commit and push to `web-backend`; Render redeploys. The
service refuses to start while any of them is still a placeholder.

This is the one file you edit on a deployment branch. `split-branches.sh` keeps
`web-backend`'s own `service/config.py` every time it rebuilds the branch, so the
credentials never reach `main` or the other branches, and on `main` the file keeps its
placeholders. `config.py` holds the credentials only: everything else the deployment needs
(the web app's address, CORS, retention) is in `service/deployment.py`, which updates from
`main` like any other code.

The keys are readable by anyone who can read the repository. That is the alpha's
choice for simplicity; keep the repository private, and move the keys to environment
variables before sharing it or the app more widely.

## 4. Render (the service)

1. In Render, **New → Blueprint**, connect the GitHub repository, and pick the
   **`web-backend`** branch. Render reads `render.yaml`: one Docker web service,
   `epm-service`, built from `service/Dockerfile`, health-checked at `/api/health`, on the
   Starter plan (change `plan` in `render.yaml` for another).
2. Wait for the first deploy, then note the service's URL, such as
   `https://epm-service.onrender.com`.
3. Check it from a checkout of the repository:

```text
pip install ".[plot]" -r service/requirements.txt
python -m service.smoke https://epm-service.onrender.com
```

The smoke test checks health, copies the example, saves an edit, runs the model, exports
it, reads it back over MCP, and deletes the copy.

## 5. Vercel (the web app)

1. In Vercel, **Add New → Project** and import the repository.
2. Set **Root Directory** to `frontend-web`. The framework (Vite), build command and output
   directory come from `frontend-web/vercel.json`.
3. Under **Environment Variables**, set `VITE_API_URL` to the Render URL from step 4, with
   no trailing slash. It is read at build time, so redeploy after changing it.
4. Under **Settings → Git**, set the **Production Branch** to **`web-frontend`**. Other
   branches get preview deployments unless you turn those off there.

## 6. Links back to the web app

`WEB_URL` in `service/deployment.py` is the web app's address
(`https://epm-alpha.nommo.cloud`), so MCP's `get_project` returns `web_url`, a link to the
project in the web app. `PUBLIC_URL` can stay empty: the service takes its own address from
each request. Change them on `main`; the service redeploys once CI passes.

## Day to day

- **Ship a change:** merge it into `main`. After CI passes, the branches move and Render and
  Vercel deploy on their own.
- **Roll back a deploy:** use Render's and Vercel's own rollback to an earlier deploy, or
  revert the change on `main`.
- **Data:** every project version is kept in R2 under `projects/`, and the metadata is
  backed up daily to `backups/meta/` in the same bucket. Supabase keeps its own backups
  as well, depending on your plan.
