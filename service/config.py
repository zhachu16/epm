"""The service's credentials.

The alpha keeps them here, in code, on purpose. On `main` this file keeps its placeholders;
the real values live only in the copy on the `web-backend` branch, which
`scripts/split-branches.sh` never overwrites. Everything else the deployment needs is in
`deployment.py`, which updates from `main` as usual. Anyone who can read the repository can
read the credentials, so rotate them and move them to environment variables before the
repository or the app is shared further.
"""

from __future__ import annotations

# Supabase: Project Settings → API. Use the service-role key (server-side only).
SUPABASE_URL = "https://YOUR-PROJECT-REF.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR-SUPABASE-SERVICE-ROLE-KEY"

# Cloudflare R2: R2 → Manage API tokens (Object Read & Write on the bucket).
R2_ACCOUNT_ID = "YOUR-CLOUDFLARE-ACCOUNT-ID"
R2_ACCESS_KEY_ID = "YOUR-R2-ACCESS-KEY-ID"
R2_SECRET_ACCESS_KEY = "YOUR-R2-SECRET-ACCESS-KEY"
R2_BUCKET = "epm"


def configured() -> bool:
    """Whether the placeholders have been replaced."""
    values = [SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID]
    return not any("YOUR-" in v for v in values + [R2_SECRET_ACCESS_KEY])
