"""Serve a scratch copy of the LBO example for the end-to-end tests.

Usage: python e2e/serve.py [PORT]. The copy is removed when the server stops.
"""

import shutil
import signal
import sys
import tempfile
from pathlib import Path

from epm.server import serve

ROOT = Path(__file__).resolve().parents[2]

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
scratch = Path(tempfile.mkdtemp(prefix="epm-e2e-"))
model = scratch / "lbo"
ignore = shutil.ignore_patterns("cache", ".epm-session.lock")
shutil.copytree(ROOT / "examples" / "lbo", model, ignore=ignore)
# uvicorn re-raises the SIGTERM that stopped it; exit normally then so the copy is removed.
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    serve(model, port=port, frontend=ROOT / "frontend" / "dist")
finally:
    shutil.rmtree(scratch, ignore_errors=True)
