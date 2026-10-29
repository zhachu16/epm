import type { Lag, MetricInfo } from "../api/types";

export interface GraphNode {
  id: string;
  group: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  lagged: boolean; // read through prev()
  whole: boolean; // reads the whole series (reductions)
  count: number; // edges merged into this one when groups are collapsed
}

export interface GroupInfo {
  id: string;
  members: string[];
  collapsed: boolean;
}

export interface GraphModel {
  nodes: GraphNode[]; // visible metric nodes (members of expanded groups)
  groups: GroupInfo[];
  edges: GraphEdge[]; // between visible nodes and/or collapsed groups
}

export const groupNodeId = (group: string) => `group:${group}`;

/**
 * Metrics as nodes inside groups (tables, else top-level folders). Collapsing a group
 * replaces its members with one node and merges the edges that touch them.
 */
export function buildGraph(
  metrics: Record<string, MetricInfo>,
  collapsed: string[] = [],
  only?: Set<string>,
): GraphModel {
  const closed = new Set(collapsed);
  const names = Object.keys(metrics).filter((n) => !only || only.has(n));
  const visible = new Set(names);
  const groups = new Map<string, string[]>();
  for (const name of names) {
    const g = metrics[name].group;
    groups.set(g, [...(groups.get(g) ?? []), name]);
  }
  const endpoint = (name: string) => {
    const g = metrics[name].group;
    return closed.has(g) ? groupNodeId(g) : name;
  };
  const edges = new Map<string, GraphEdge>();
  for (const target of names) {
    for (const [source, lag] of Object.entries(metrics[target].deps) as [string, Lag][]) {
      if (!visible.has(source)) continue;
      const s = endpoint(source);
      const t = endpoint(target);
      if (s === t) continue;
      const id = `${s}->${t}`;
      const existing = edges.get(id);
      const lagged = typeof lag === "number" && lag > 0;
      const whole = lag === null;
      if (existing) {
        existing.count += 1;
        existing.lagged &&= lagged;
        existing.whole ||= whole;
      } else {
        edges.set(id, { id, source: s, target: t, lagged, whole, count: 1 });
      }
    }
  }
  return {
    nodes: names.filter((n) => !closed.has(metrics[n].group)).map((id) => ({ id, group: metrics[id].group })),
    groups: [...groups.entries()].map(([id, members]) => ({ id, members, collapsed: closed.has(id) })),
    edges: [...edges.values()],
  };
}

/** A metric and everything it depends on, or everything that depends on it. */
export function neighbourhood(
  metrics: Record<string, MetricInfo>,
  start: string,
  direction: "upstream" | "downstream" | "both",
): Set<string> {
  const out = new Set<string>([start]);
  const walk = (next: (n: string) => string[]) => {
    const stack = [start];
    while (stack.length) {
      const n = stack.pop()!;
      for (const m of next(n)) {
        if (!out.has(m) && metrics[m]) {
          out.add(m);
          stack.push(m);
        }
      }
    }
  };
  if (direction !== "downstream") walk((n) => Object.keys(metrics[n]?.deps ?? {}));
  if (direction !== "upstream") walk((n) => metrics[n]?.used_by ?? []);
  return out;
}

/** A key that changes only when the drawn structure changes (not when values do). */
export function structureKey(model: GraphModel): string {
  return JSON.stringify([
    model.nodes.map((n) => n.id),
    model.groups.map((g) => [g.id, g.collapsed]),
    model.edges.map((e) => e.id),
  ]);
}
