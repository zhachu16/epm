"""Service configuration.

The alpha keeps credentials here, in code, on purpose: fill in the placeholders by hand.
Anyone who can read this repository can read them, so rotate them and move them to
environment variables before the repository or the app is shared further.
"""

from __future__ import annotations

# Supabase: Project Settings → API. Use the service-role key (server-side only).
SUPABASE_URL = "https://canyjeptahckhsfibvti.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNhbnlqZXB0YWhja2hzZmlidnRpIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MzI5NTM2OCwiZXhwIjoyMTA4ODcxMzY4fQ.J8FDhh0L6QUc0u6VQR88CaC213x6uwdMhl_hMqscE18"

# Cloudflare R2: R2 → Manage API tokens (Object Read & Write on the bucket).
R2_ACCOUNT_ID = "a590271adca2f3691715b191174c8339"
R2_ACCESS_KEY_ID = "d79580e1fecbfd1eb3b98875e6b7abe8"
R2_SECRET_ACCESS_KEY = "8a49326b7194332dca0a1390919299e0a1ac594ead41ce1a39ace0cd453eb888"
R2_BUCKET = "epm"

# The browser origins allowed to call the API. The alpha is open: any origin.
CORS_ORIGINS = ["*"]

# Where the service and the web app are reachable, for links in MCP results. Empty: the
# service URL is taken from the request, and project links to the web app are left out.
PUBLIC_URL = ""
WEB_URL = ""

# Retention: the newest versions kept per project, then one per day for this many days.
KEEP_VERSIONS = 50
KEEP_DAILY_DAYS = 90
# Deleted projects can be restored for this many days, then they are purged.
KEEP_DELETED_DAYS = 30
# How often the retention and backup job runs, in seconds.
MAINTENANCE_INTERVAL = 24 * 60 * 60


def configured() -> bool:
    """Whether the placeholders have been replaced."""
    values = [SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID]
    return not any("YOUR-" in v for v in values + [R2_SECRET_ACCESS_KEY])
