"""Time the operations people and agents wait on.

Usage: python benchmarks/run.py [--scenarios 1024] [--metrics 2000]

* the sample LBO: first run, editing an input (preview and commit), and two sweeps — one
  whose input only affects the returns, one that re-solves the circular debt schedule;
* a synthetic model of ``--metrics`` metrics: building, running and editing it.
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from epm import Workspace, load_model

ROOT = Path(__file__).resolve().parents[1]


def timed(label: str, fn: Callable[[], Any], per: int | None = None) -> Any:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    extra = f"  ({elapsed / per * 1000:.1f} ms each)" if per else ""
    print(f"  {label:<44} {elapsed:7.3f} s{extra}")
    return result


def lbo(scenarios: int) -> None:
    print("sample LBO")
    scratch = Path(tempfile.mkdtemp(prefix="epm-bench-"))
    try:
        target = scratch / "lbo"
        ignore = shutil.ignore_patterns("cache", ".epm-session.lock")
        shutil.copytree(ROOT / "examples" / "lbo", target, ignore=ignore)
        ws = Workspace.open(target)
        timed("first run", ws.run)
        edit = [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}]
        timed("preview an edit", lambda: ws.apply(edit, mode="dry_run"))
        timed("commit an edit", lambda: ws.apply(edit))
        side = round(scenarios**0.5)
        grid = list(np.round(np.linspace(7, 11, side), 4))
        n = side * side
        timed(
            f"sweep {n} entry × exit multiples",
            lambda: ws.sweep({"entry_multiple": grid, "exit_multiple": grid}, ["equity_irr"]),
            per=n,
        )
        rates = list(np.round(np.linspace(0.03, 0.09, scenarios), 6))
        timed(
            f"sweep {scenarios} interest rates (debt re-solved)",
            lambda: ws.sweep({"interest_rate": rates}, ["equity_irr", "moic"]),
            per=scenarios,
        )
        ws.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def synthetic(n: int) -> None:
    """Chains of growth recurrences, sums and element-wise functions over 40 periods."""
    print(f"synthetic model, {n} metrics")
    rng = random.Random(1)
    metrics: dict[str, Any] = {"growth": {"value": 0.03}, "base": {"value": 100}}
    names = ["base"]
    for i in range(n):
        a, b, name = rng.choice(names), rng.choice(names), f"m{i}"
        formula = rng.choice(
            [
                f"prev({name}, init={a}) * (1 + growth)",
                f"{a} + {b} * 0.5",
                f"max({a} - {b}, 0)",
                f"where(is_forecast(), {a}, {b}) / 2",
            ]
        )
        metrics[name] = {"formula": formula}
        names.append(name)
    model = load_model(
        {
            "settings": {"default_axis": "y"},
            "axes": {"y": {"values": list(range(2000, 2040)), "historical": [2000, 2001]}},
            "metrics": metrics,
        }
    )
    ws = Workspace(model)
    timed("build", lambda: ws.engine)
    timed("run", ws.run)
    root = [{"op": "update", "kind": "metric", "name": "growth", "set": {"value": 0.04}}]
    timed("edit an input everything depends on", lambda: ws.apply(root))
    leaf = [{"op": "update", "kind": "metric", "name": f"m{n - 1}", "set": {"formula": "base"}}]
    timed("edit one formula", lambda: ws.apply(leaf))
    ws.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=int, default=1024)
    parser.add_argument("--metrics", type=int, default=2000)
    args = parser.parse_args()
    lbo(args.scenarios)
    synthetic(args.metrics)
