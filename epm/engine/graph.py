"""Dependency graph over metrics, with lag-labelled edges."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

Lag = int | None  # None means the whole series is needed


class DependencyGraph:
    """``deps[m]`` maps each metric that ``m`` reads to the lag it reads it at."""

    def __init__(self, deps: dict[str, dict[str, Lag]]):
        self.deps = deps
        self.dependants: dict[str, dict[str, Lag]] = {name: {} for name in deps}
        for name, ds in deps.items():
            for d, lag in ds.items():
                self.dependants.setdefault(d, {})[name] = lag

    def upstream(self, name: str, depth: int | None = None) -> list[str]:
        return _bfs(name, self.deps, depth)

    def downstream(self, name: str, depth: int | None = None) -> list[str]:
        return _bfs(name, self.dependants, depth)

    def closure(self, names: Iterable[str]) -> set[str]:
        """``names`` and everything they depend on."""
        seen: set[str] = set()
        stack = list(names)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.deps.get(n, ()))
        return seen

    def components(self) -> list[list[str]]:
        """Strongly connected components, dependencies before dependants.

        Iterative Tarjan. Following edges from a metric to its dependencies, Tarjan emits
        components in reverse topological order, which is exactly evaluation order.
        """
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        out: list[list[str]] = []
        counter = 0
        position = {name: i for i, name in enumerate(self.deps)}

        for root in self.deps:
            if root in index:
                continue
            work = [(root, iter(self.deps.get(root, ())))]
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            while work:
                node, children = work[-1]
                advanced = False
                for child in children:
                    if child not in self.deps:
                        continue
                    if child not in index:
                        index[child] = low[child] = counter
                        counter += 1
                        stack.append(child)
                        on_stack.add(child)
                        work.append((child, iter(self.deps.get(child, ()))))
                        advanced = True
                        break
                    if child in on_stack:
                        low[node] = min(low[node], index[child])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        n = stack.pop()
                        on_stack.discard(n)
                        comp.append(n)
                        if n == node:
                            break
                    out.append(sorted(comp, key=position.__getitem__))
        return out


def _bfs(start: str, edges: dict[str, dict[str, Lag]], depth: int | None) -> list[str]:
    seen = {start}
    order: list[str] = []
    queue = deque([(start, 0)])
    while queue:
        node, d = queue.popleft()
        if depth is not None and d >= depth:
            continue
        for nxt in edges.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                order.append(nxt)
                queue.append((nxt, d + 1))
    return order


def find_cycle(nodes: Iterable[str], edges: dict[str, list[str]]) -> list[str] | None:
    """Return a cycle ``[a, b, ..., a]`` (each depends on the next) among ``nodes``, if any."""
    members = set(nodes)
    color: dict[str, int] = {}
    for root in members:
        if color.get(root):
            continue
        path: list[str] = []
        work = [(root, iter(edges.get(root, ())))]
        color[root] = 1
        path.append(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in members:
                    continue
                c = color.get(child, 0)
                if c == 1:
                    return [*path[path.index(child) :], child]
                if c == 0:
                    color[child] = 1
                    path.append(child)
                    work.append((child, iter(edges.get(child, ()))))
                    advanced = True
                    break
            if not advanced:
                work.pop()
                color[node] = 2
                path.pop()
    return None


def topo_order(nodes: Iterable[str], edges: dict[str, list[str]]) -> list[str]:
    """Order acyclic ``nodes`` so that dependencies (``edges[n]``) come first."""
    members = list(nodes)
    member_set = set(members)
    done: set[str] = set()
    out: list[str] = []
    for root in members:
        if root in done:
            continue
        work = [(root, iter(edges.get(root, ())))]
        done.add(root)
        while work:
            node, children = work[-1]
            for child in children:
                if child in member_set and child not in done:
                    done.add(child)
                    work.append((child, iter(edges.get(child, ()))))
                    break
            else:
                work.pop()
                out.append(node)
    return out
