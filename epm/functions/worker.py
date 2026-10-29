"""Worker process that runs functions.py in isolation.

The worker executes the user's code with a restricted set of builtins and an import
allowlist, under OS resource limits on memory and CPU time. This contains runaway or
careless code; it is not a security boundary against malicious code (numpy and pandas can
still read files, for example), so only open models whose functions.py you trust.
"""

from __future__ import annotations

import builtins
import os
import traceback

from .channel import Channel

ALLOWED_MODULES = {
    "math",
    "cmath",
    "statistics",
    "decimal",
    "fractions",
    "datetime",
    "itertools",
    "functools",
    "operator",
    "collections",
    "dataclasses",
    "typing",
    "numbers",
    "bisect",
    "heapq",
    "enum",
    "re",
    "numpy",
    "pandas",
}
BLOCKED_BUILTINS = {
    "open",
    "exec",
    "eval",
    "compile",
    "input",
    "breakpoint",
    "help",
    "exit",
    "quit",
}


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level:
        raise ImportError("relative imports are not allowed in functions.py")
    root = name.split(".")[0]
    if root == "epm" and name == "epm" and set(fromlist or ()) <= {"function"}:
        return builtins.__import__(name, globals, locals, fromlist, level)
    if root not in ALLOWED_MODULES:
        allowed = ", ".join(sorted(ALLOWED_MODULES))
        raise ImportError(f"import of '{name}' is not allowed in functions.py (allowed: {allowed})")
    return builtins.__import__(name, globals, locals, fromlist, level)


def load(source: str) -> dict:
    safe = {k: v for k, v in vars(builtins).items() if k not in BLOCKED_BUILTINS}
    safe["__import__"] = _guarded_import
    namespace = {"__builtins__": safe, "__name__": "epm_functions"}
    exec(compile(source, "functions.py", "exec"), namespace)  # noqa: S102 - the point of the worker
    return namespace


def _vm_bytes() -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmSize:"):
                return int(line.split()[1]) * 1024
    raise OSError("no VmSize")


def _limit_memory(memory_mb: int) -> None:
    try:
        import resource

        limit = _vm_bytes() + memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, OSError, ValueError):
        pass  # not available on this platform; the wall-clock timeout still applies


def _limit_cpu(seconds: int) -> None:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        used = usage.ru_utime + usage.ru_stime
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        soft = int(used) + seconds + 1
        if hard != resource.RLIM_INFINITY:
            soft = min(soft, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ImportError, OSError, ValueError):
        pass


def serve(channel: Channel) -> None:
    kind, source, memory_mb, cpu_seconds = channel.recv()
    assert kind == "init"
    try:
        namespace = load(source)
    except BaseException as e:  # noqa: BLE001
        channel.send(("load_error", f"{type(e).__name__}: {e}"))
        return
    _limit_memory(memory_mb)
    channel.send(("ready", None))
    while True:
        try:
            message = channel.recv()
        except EOFError:
            return
        if message[0] == "stop":
            return
        _, name, args, kwargs = message
        _limit_cpu(cpu_seconds)
        try:
            fn = namespace.get(name)
            if fn is None or not hasattr(fn, "__epm_function__"):
                raise NameError(f"functions.py has no @function named '{name}'")
            channel.send(("ok", fn(*args, **kwargs)))
        except MemoryError:
            channel.send(("error", "MemoryError", "memory limit exceeded"))
        except BaseException as e:  # noqa: BLE001 - report anything the user code raises
            tb = traceback.extract_tb(e.__traceback__)
            where = next(
                (f"line {f.lineno}" for f in reversed(tb) if f.filename == "functions.py"), None
            )
            text = f"{e}" + (f" (functions.py {where})" if where else "")
            try:
                channel.send(("error", type(e).__name__, text))
            except Exception:  # noqa: BLE001 - e.g. the result could not be pickled
                channel.send(("error", type(e).__name__, "error result could not be sent"))


def main() -> None:
    # Keep the protocol on private copies of stdin/stdout; anything the user's code prints
    # goes to stderr instead of corrupting the frames.
    reader = os.fdopen(os.dup(0), "rb")
    writer = os.fdopen(os.dup(1), "wb")
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(2, 1)
    serve(Channel(reader, writer))


if __name__ == "__main__":
    main()
