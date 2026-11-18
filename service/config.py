"""The deployment's secrets and paths, read from environment variables.

Nothing secret is kept in the repository. Set these where the service runs (a ``.env`` file
for Docker Compose, for example):

    API_TOKEN              the secret in the service's URLs, at least 32 URL-safe characters
    R2_ACCOUNT_ID          Cloudflare account ID
    R2_ACCESS_KEY_ID       R2 API token with Object Read & Write on the bucket
    R2_SECRET_ACCESS_KEY
    R2_BUCKET              the bucket for model archives, files and outputs (default: epm)
    EPM_DATA               the directory of the records database (default: data)

Everything that isn't secret is in ``deployment.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DATABASE_NAME = "epm.db"


@dataclass(frozen=True)
class Config:
    api_token: str
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    data: Path

    @property
    def database(self) -> Path:
        return self.data / DATABASE_NAME

    def missing(self) -> list[str]:
        """The required environment variables that are not set."""
        required = {
            "API_TOKEN": self.api_token,
            "R2_ACCOUNT_ID": self.r2_account_id,
            "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
        }
        return [name for name, value in required.items() if not value]


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env

    def get(name: str, default: str = "") -> str:
        return env.get(name, "").strip() or default

    return Config(
        api_token=get("API_TOKEN"),
        r2_account_id=get("R2_ACCOUNT_ID"),
        r2_access_key_id=get("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=get("R2_SECRET_ACCESS_KEY"),
        r2_bucket=get("R2_BUCKET", "epm"),
        data=Path(get("EPM_DATA", "data")),
    )
