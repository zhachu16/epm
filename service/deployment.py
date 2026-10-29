"""Settings of the deployment that aren't secret: where the service and the web app live,
which browser origins may call the API, and how long data is kept.

Unlike ``config.py``, which holds the credentials and is kept on the ``web-backend`` branch,
this file reaches the deployment from ``main`` like any other code.
"""

from __future__ import annotations

# The browser origins allowed to call the API. The alpha is open: any origin.
CORS_ORIGINS = ["*"]

# Where the service and the web app are reachable, for links in MCP results. An empty
# PUBLIC_URL takes the service's address from each request; an empty WEB_URL leaves project
# links out.
PUBLIC_URL = ""
WEB_URL = "https://epm-alpha.nommo.cloud"

# Retention: the newest versions kept per project, then one per day for this many days.
KEEP_VERSIONS = 50
KEEP_DAILY_DAYS = 90
# Deleted projects can be restored for this many days, then they are purged.
KEEP_DELETED_DAYS = 30
# How often the retention and backup job runs, in seconds.
MAINTENANCE_INTERVAL = 24 * 60 * 60
