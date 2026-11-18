"""Load project records into the service's SQLite database, replacing what is there.

    python -m service.migrate supabase           # from the Supabase project epm used before
    python -m service.migrate backup [DATE]      # from a daily backup in the R2 bucket
    python -m service.migrate file DUMP.json     # from a dump saved as a file

``supabase`` reads the ``projects``, ``versions`` and ``outputs`` tables over Supabase's
REST API; set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment for that one
run. ``backup`` reads ``backups/meta/<DATE>.json`` (the newest when DATE is omitted), which
the service writes every day. The files themselves are not touched: they stay in R2.

Stop the service first. A database that already holds projects of its own is left alone
unless ``--replace`` is given. ``--check-files`` also confirms that every file the records
refer to exists in the bucket.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import config
from .projects import EXAMPLE_ID
from .store.sqlite import TABLES, SqliteMetaStore

Dump = dict[str, list[dict[str, Any]]]
BACKUPS = "backups/meta/"
_PAGE = 1000  # PostgREST's default row limit
_ORDER = {"projects": "id", "versions": "project_id,number", "outputs": "id"}


class MigrateError(Exception):
    pass


def from_supabase(url: str, key: str, page: int = _PAGE) -> Dump:
    """Every row of the three tables, read through PostgREST."""
    dump: Dump = {}
    for table, order in _ORDER.items():
        rows: list[dict[str, Any]] = []
        while True:
            query = urllib.parse.urlencode(
                {"select": "*", "order": order, "limit": page, "offset": len(rows)}
            )
            request = urllib.request.Request(
                f"{url.rstrip('/')}/rest/v1/{table}?{query}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    batch = json.loads(response.read())
            except urllib.error.HTTPError as e:
                raise MigrateError(f"Supabase answered {e.code} for '{table}'") from None
            except urllib.error.URLError as e:
                raise MigrateError(f"could not reach Supabase: {e.reason}") from None
            if not batch:  # an empty page, not a short one: the server may cap pages lower
                break
            rows.extend(batch)
        dump[table] = rows
    return dump


def from_backup(blobs, date: str | None = None) -> tuple[str, Dump]:
    """The daily backup of ``date`` (default: the newest) in the bucket, and its key."""
    if date:
        key = f"{BACKUPS}{date}.json"
    else:
        keys = sorted(k for k in blobs.keys(BACKUPS) if k.endswith(".json"))
        if not keys:
            raise MigrateError(f"the bucket has no backups under {BACKUPS}")
        key = keys[-1]
    try:
        return key, json.loads(blobs.get(key))
    except KeyError:
        raise MigrateError(f"the bucket has no {key}") from None


def check(dump: Dump) -> None:
    """Refuse a dump that cannot be a set of project records."""
    if not isinstance(dump, dict) or not all(isinstance(dump.get(t), list) for t in TABLES):
        raise MigrateError(f"not a records dump: it needs the lists {', '.join(TABLES)}")
    ids = {p["id"] for p in dump["projects"]}
    for table in ("versions", "outputs"):
        orphans = {r["project_id"] for r in dump[table]} - ids
        if orphans:
            raise MigrateError(f"{table} refer to unknown projects: {sorted(orphans)[:5]}")
    numbers = {(v["project_id"], v["number"]) for v in dump["versions"]}
    for p in dump["projects"]:
        if (p["id"], p["current_version"]) not in numbers:
            raise MigrateError(f"project '{p['id']}' has no version {p['current_version']}")


def missing_files(dump: Dump, blobs) -> list[str]:
    """The files the records refer to that are not in the bucket."""
    stored = set(blobs.keys("projects/"))
    wanted = [r["blob_key"] for table in ("versions", "outputs") for r in dump[table]]
    return sorted(set(wanted) - stored)


def load(meta: SqliteMetaStore, dump: Dump, replace: bool = False) -> dict[str, int]:
    check(dump)
    own = [p.id for deleted in (False, True) for p in meta.list_projects(deleted=deleted)]
    own = [i for i in own if i != EXAMPLE_ID]
    if own and not replace:
        raise MigrateError(
            f"{meta.path} already holds {len(own)} project(s); pass --replace to overwrite them"
        )
    return meta.load(dump)


def _blobs(settings: config.Config):
    from .store.r2 import R2BlobStore

    absent = [n for n in settings.missing() if n.startswith("R2_")]
    if absent:
        raise MigrateError(f"set {', '.join(absent)} in the environment")
    return R2BlobStore(
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.r2_bucket,
    )


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(
        prog="python -m service.migrate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--replace", action="store_true", help="overwrite existing projects")
    parser.add_argument(
        "--check-files", action="store_true", help="confirm the records' files are in R2"
    )
    sources = parser.add_subparsers(dest="source", required=True)
    sources.add_parser("supabase")
    sources.add_parser("backup").add_argument("date", nargs="?", help="YYYY-MM-DD")
    sources.add_parser("file").add_argument("path", type=Path)
    args = parser.parse_args(argv)
    settings = config.load(env)
    try:
        if args.source == "supabase":
            url, key = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", "")
            if not (url and key):
                raise MigrateError("set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
            origin, dump = "Supabase", from_supabase(url, key)
        elif args.source == "backup":
            origin, dump = from_backup(_blobs(settings), args.date)
        else:
            origin, dump = str(args.path), json.loads(args.path.read_text())
        meta = SqliteMetaStore(settings.database)
        try:
            counts = load(meta, dump, args.replace)
        finally:
            meta.close()
        print(f"loaded {origin} into {settings.database}:")
        for table, n in counts.items():
            print(f"  {n:6d} {table}")
        if args.check_files:
            lost = missing_files(dump, _blobs(settings))
            if lost:
                print(f"{len(lost)} file(s) are not in the bucket '{settings.r2_bucket}':")
                for key in lost[:20]:
                    print(f"  {key}")
                return 1
            print(f"every file is in the bucket '{settings.r2_bucket}'")
    except (MigrateError, json.JSONDecodeError, OSError) as e:
        print(f"FAILED  {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
