"""Turn the dependency graph into evaluation steps.

Each strongly connected component of the graph becomes one step:

* ``single``: one metric with no self-reference, evaluated over the whole axis at once;
* ``circular``: a declared same-period loop with no lagged edges inside it, solved by
  iterating over whole series (scalars and series may mix);
* ``recurrence``: a component whose loops pass through ``prev()``. It is evaluated one
  period at a time; within a period, its blocks run in same-period dependency order, and a
  declared circular block inside it is iterated to convergence at that period.

A same-period loop that is not declared under ``circularities`` is rejected.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from ..formula import FULL, SCALAR
from ..formula.shapes import Shape, describe
from ..model.schema import Circularity
from .graph import DependencyGraph, Lag, find_cycle, topo_order


@dataclass
class Block:
    """Metrics evaluated together within one period: one metric, or a declared loop."""

    members: list[str]
    circularity: str | None = None


@dataclass
class Step:
    kind: Literal["single", "circular", "recurrence"]
    members: list[str]  # evaluation order
    blocks: list[Block] = field(default_factory=list)  # recurrence only
    circularity: str | None = None  # circular only


Fail = Callable[[str, str, str, dict], None]  # (metric, code, message, detail)


def plan(
    refs: dict[str, dict[str, Lag]],
    shapes: dict[str, Shape],
    circularities: dict[str, Circularity],
    fail: Fail,
) -> tuple[list[Step], set[str]]:
    """Return the steps and the set of circularities that cover an actual loop."""
    owner = {m: name for name, c in circularities.items() for m in c.metrics}
    used: set[str] = set()
    steps: list[Step] = []

    for comp in DependencyGraph(refs).components():
        members = set(comp)
        if len(comp) == 1 and comp[0] not in refs[comp[0]]:
            steps.append(Step("single", comp))
            continue

        same_period = {
            m: [d for d, lag in refs[m].items() if d in members and not lag] for m in comp
        }
        lagged = any(lag for m in comp for d, lag in refs[m].items() if d in members and lag)

        blocks, problem = _blocks(comp, same_period, owner)
        if problem is not None:
            code, message, detail = problem
            for m in comp:
                fail(m, code, message, detail)
            steps.extend(Step("single", [m]) for m in comp)
            continue
        used.update(b.circularity for b in blocks if b.circularity)

        if not lagged:
            # one declared loop within a single period: iterate whole series together
            [block] = blocks
            steps.append(Step("circular", block.members, circularity=block.circularity))
            continue

        noncausal = [
            (m, d) for m in comp for d, lag in refs[m].items() if d in members and lag is FULL
        ]
        if noncausal:
            m, d = noncausal[0]
            message = (
                f"'{m}' reads every period of '{d}' (e.g. total() or next()), but '{d}' "
                f"depends on '{m}' through prev(); this cannot be evaluated period by period"
            )
            for name in comp:
                fail(name, "noncausal_recurrence", message, {"pair": [m, d]})
            steps.extend(Step("single", [m]) for m in comp)
            continue

        axes = {shapes[m] for m in comp}
        if len(axes) != 1 or SCALAR in axes:
            message = (
                "metrics in a period recurrence must all be series on one axis; got "
                + ", ".join(f"{m}: {describe(shapes[m])}" for m in comp)
                + "; declare an axis on the metric or set settings.default_axis"
            )
            for name in comp:
                fail(name, "recurrence_axis", message, {})
            steps.extend(Step("single", [m]) for m in comp)
            continue

        order = [m for b in blocks for m in b.members]
        steps.append(Step("recurrence", order, blocks=blocks))
    return steps, used


def _blocks(
    comp: list[str], same_period: dict[str, list[str]], owner: dict[str, str]
) -> tuple[list[Block], tuple[str, str, dict] | None]:
    """Split a component into same-period blocks in dependency order.

    Returns an error ``(code, message, detail)`` for any same-period loop that is not
    entirely inside one declared circularity.
    """
    inner = DependencyGraph({m: dict.fromkeys(same_period[m], 0) for m in comp}).components()
    blocks = []
    for group in inner:
        cyclic = len(group) > 1 or group[0] in same_period[group[0]]
        if not cyclic:
            blocks.append(Block(group))
            continue
        declared = {owner.get(m) for m in group}
        if len(declared) == 1 and None not in declared:
            name = declared.pop()
            blocks.append(Block(topo_order(group, same_period), name))
            continue
        path = find_cycle(group, same_period) or group
        message = f"circular reference: {' → '.join(path)}"
        if len(path) == 2:
            message += f"; use prev({path[0]}) to refer to the previous period"
        missing = [m for m in group if owner.get(m) is None]
        names = sorted({owner[m] for m in group if owner.get(m)})
        if names and missing:
            message += f"; {', '.join(missing)} not in circularity '{names[0]}'"
        elif len(names) > 1:
            message += f"; the loop spans circularities {', '.join(names)}"
        else:
            ordered = [m for m in comp if m in group]
            message += (
                "; to solve it iteratively, declare these metrics under 'circularities': "
                + ", ".join(ordered)
            )
        return [], ("circular_reference", message, {"path": path, "members": sorted(group)})
    return blocks, None
