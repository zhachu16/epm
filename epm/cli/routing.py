"""Forward ``fm`` commands to a running ``fm serve`` session for the same model.

The session keeps undo history and pending proposals in memory, so commands must run
there to share them. Without a session, commands run locally as usual.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ..errors import EpmError

LOCAL_COMMANDS = {"serve", "init", "pack", "unpack"}


def split_global(argv: list[str]) -> tuple[str | None, list[str]]:
    """The model option (if any) and the arguments from the subcommand on."""
    model = os.environ.get("EPM_MODEL")
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-m", "--model") and i + 1 < len(argv):
            model, i = argv[i + 1], i + 2
        elif arg.startswith("--model="):
            model, i = arg.split("=", 1)[1], i + 1
        elif arg.startswith("-"):
            i += 1
        else:
            break
    return model, argv[i:]


def forward(argv: list[str]) -> dict | None:
    """Run ``argv`` in a live session if one serves this model; ``None`` otherwise."""
    from ..server.lockfile import base_url, live_session
    from .app import find_model

    if any(a in ("--help", "-h") for a in argv):
        return None
    model, rest = split_global(argv)
    if not rest or rest[0] in LOCAL_COMMANDS:
        return None
    try:
        path = find_model(Path(model) if model else None)
    except EpmError:
        return None
    info = live_session(path)
    if info is None:
        return None
    request = urllib.request.Request(
        base_url(info) + "/api/cli",
        data=json.dumps({"argv": argv}).encode(),
        headers={"Content-Type": "application/json", "X-EPM-Token": info["token"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310 - local
            return json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None  # the session went away; run locally instead


def run(argv: list[str] | None = None) -> None:
    from .app import app

    argv = sys.argv[1:] if argv is None else argv
    result = forward(argv)
    if result is None:
        app(args=argv, prog_name="fm")
        return
    sys.stdout.write(result["stdout"])
    sys.stderr.write(result["stderr"])
    sys.exit(result["exit_code"])
