"""Run custom functions in an isolated worker process with resource limits."""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import os
import pickle
import select
import signal
import subprocess
import sys
import tempfile
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .channel import Channel


@dataclass(frozen=True)
class Limits:
    timeout: float = 30.0  # wall-clock seconds per call
    cpu_seconds: int = 30  # CPU seconds per call
    memory_mb: int = 1024  # address space the function may add on top of the worker's own
    startup_timeout: float = 120.0


class FunctionCallError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_RUNTIMES: weakref.WeakSet[FunctionRuntime] = weakref.WeakSet()


@atexit.register
def _shutdown_all() -> None:
    for runtime in list(_RUNTIMES):
        runtime.close()


class FunctionRuntime:
    """One worker per runtime, started on the first call and restarted after a failure.

    Results are memoized by function name and argument values, so identical calls from
    several metrics, or from repeated runs, execute once.
    """

    def __init__(self, source: str, limits: Limits | None = None, memo_size: int = 10_000):
        self.source = source
        self.limits = limits or Limits()
        self.memo: OrderedDict[str, Any] = OrderedDict()
        self.memo_size = memo_size
        self.calls = 0  # calls that reached the worker
        self._proc: subprocess.Popen | None = None
        self._channel: Channel | None = None
        _RUNTIMES.add(self)

    def _start(self) -> None:
        env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        # -I: isolated mode, so the current directory and user site-packages are not on
        # sys.path and a stray module there cannot shadow anything the worker imports
        proc = subprocess.Popen(
            [sys.executable, "-I", "-m", "epm.functions.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            cwd=tempfile.gettempdir(),
        )
        channel = Channel(proc.stdout, proc.stdin)
        self._proc, self._channel = proc, channel
        try:
            channel.send(("init", self.source, self.limits.memory_mb, self.limits.cpu_seconds))
            if not self._ready(self.limits.startup_timeout):
                raise FunctionCallError("startup_timeout", "the function worker did not start")
            kind, payload = channel.recv()
        except (EOFError, BrokenPipeError, OSError):
            code = proc.wait(5)
            self._kill()
            raise FunctionCallError(
                "load_error", f"the function worker exited while loading (code {code})"
            ) from None
        except FunctionCallError:
            self._kill()
            raise
        if kind == "load_error":
            self._kill()
            raise FunctionCallError("load_error", f"functions.py failed to load: {payload}")

    def _ready(self, timeout: float) -> bool:
        assert self._proc is not None and self._proc.stdout is not None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        return bool(ready)

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait(5)
            for stream in (self._proc.stdin, self._proc.stdout):
                if stream is not None:
                    stream.close()
        self._proc = self._channel = None

    def close(self) -> None:
        if self._channel is not None:
            with contextlib.suppress(BrokenPipeError, OSError, ValueError):
                self._channel.send(("stop",))
        self._kill()

    def call(self, name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        try:
            key = name + ":" + hashlib.sha256(pickle.dumps((args, kwargs), protocol=5)).hexdigest()
        except Exception:  # noqa: BLE001 - unpicklable arguments cannot be sent anyway
            raise FunctionCallError("invalid_argument", "arguments cannot be serialized") from None
        if key in self.memo:
            self.memo.move_to_end(key)
            return self.memo[key]
        result = self._call(name, args, kwargs)
        self.memo[key] = result
        while len(self.memo) > self.memo_size:
            self.memo.popitem(last=False)
        return result

    def _call(self, name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if self._proc is None or self._proc.poll() is not None:
            self._kill()
            self._start()
        assert self._channel is not None and self._proc is not None
        self.calls += 1
        try:
            self._channel.send(("call", name, args, kwargs))
        except (BrokenPipeError, OSError):
            self._kill()
            raise FunctionCallError("crashed", "the function worker is not running") from None
        if not self._ready(self.limits.timeout):
            self._kill()
            raise FunctionCallError(
                "timeout", f"{name}() did not finish within {self.limits.timeout:g} seconds"
            )
        try:
            kind, *payload = self._channel.recv()
        except EOFError:
            code = self._proc.wait(5)
            self._kill()
            if code == -signal.SIGXCPU or code == -signal.SIGKILL:
                raise FunctionCallError(
                    "cpu_limit",
                    f"{name}() exceeded its CPU limit of {self.limits.cpu_seconds} seconds",
                ) from None
            raise FunctionCallError(
                "crashed", f"{name}() crashed the worker (exit {code})"
            ) from None
        if kind == "ok":
            return payload[0]
        error_type, message = payload
        code = "memory_limit" if error_type == "MemoryError" else "function_error"
        raise FunctionCallError(code, f"{name}() raised {error_type}: {message}")
