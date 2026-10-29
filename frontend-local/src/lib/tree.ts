import type { AppState, MetricInfo } from "../api/types";
import type { TabKind } from "../store";

export interface TreeNode {
  id: string;
  label: string;
  kind?: TabKind; // leaves open a tab
  name?: string;
  badge?: "error" | "warning" | "ok" | "blocked" | "failed";
  hint?: string;
  children?: TreeNode[];
}

function metricBadge(m: MetricInfo): TreeNode["badge"] {
  const state = m.status?.state;
  if (state === "error") return "error";
  if (state === "blocked") return "blocked";
  if (state === "warning") return "warning";
  return undefined;
}

/** Nest leaves under folder paths such as "Assumptions/Operations". */
function byFolder(prefix: string, leaves: (TreeNode & { folder?: string | null })[]): TreeNode[] {
  const root: TreeNode[] = [];
  const folders = new Map<string, TreeNode>();
  const folderNode = (path: string): TreeNode[] => {
    if (!path) return root;
    const existing = folders.get(path);
    if (existing) return existing.children!;
    const parts = path.split("/");
    const parent = folderNode(parts.slice(0, -1).join("/"));
    const node: TreeNode = { id: `${prefix}/${path}`, label: parts[parts.length - 1], children: [] };
    folders.set(path, node);
    parent.push(node);
    return node.children!;
  };
  for (const { folder, ...leaf } of leaves) folderNode(folder ?? "").push(leaf);
  return root;
}

function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return fields.some((f) => f?.toLowerCase().includes(q));
}

/** Build the explorer tree, filtered by a search query over names, folders and formulas. */
export function buildTree(state: AppState, query = ""): TreeNode[] {
  const model = state.model;
  const metricLeaves = (inputs: boolean) =>
    Object.entries(state.metrics)
      .filter(([, m]) => (m.kind === "value" || m.kind === "input") === inputs)
      .filter(([name, m]) => matches(query, name, m.folder, m.description, m.formula))
      .map(([name, m]) => ({
        id: `metric:${name}`,
        label: name,
        kind: "metric" as const,
        name,
        badge: metricBadge(m),
        hint: m.formula ?? undefined,
        folder: m.folder,
      }));

  const simple = (kind: TabKind, names: string[], badge?: (n: string) => TreeNode["badge"]) =>
    names.filter((n) => matches(query, n)).map((name) => ({ id: `${kind}:${name}`, label: name, kind, name, badge: badge?.(name) }));

  const sections: TreeNode[] = [
    { id: "inputs", label: "Inputs", children: byFolder("inputs", metricLeaves(true)) },
    { id: "metrics", label: "Calculations", children: byFolder("metrics", metricLeaves(false)) },
    { id: "tables", label: "Tables", children: simple("table", Object.keys(model.tables ?? {})) },
    { id: "analyses", label: "Analyses", children: simple("analysis", Object.keys(model.analyses ?? {})) },
    {
      id: "checks",
      label: "Checks",
      children: simple("check", Object.keys(model.checks ?? {}), (n) => {
        const c = state.checks[n];
        if (!c) return undefined;
        return c.state === "passed" ? "ok" : c.severity === "warning" ? "warning" : "failed";
      }),
    },
    { id: "functions", label: "Functions", children: simple("function", state.functions.map((f) => f.name)) },
    { id: "sources", label: "Sources", children: simple("source", Object.keys(model.sources ?? {})) },
    { id: "circularities", label: "Circularities", children: simple("circularity", Object.keys(model.circularities ?? {})) },
    { id: "axes", label: "Axes", children: simple("axis", Object.keys(model.axes ?? {})) },
    {
      id: "files",
      label: "Files",
      children: (
        [
          { id: "file:model.json", label: "model.json", kind: "model.json", name: "model.json" },
          { id: "file:functions.py", label: "functions.py", kind: "functions.py", name: "functions.py" },
          { id: "file:settings", label: "settings", kind: "settings", name: "settings" },
        ] satisfies TreeNode[]
      ).filter((n) => matches(query, n.label)),
    },
  ];
  return sections.filter((s) => !query || (s.children?.length ?? 0) > 0);
}

export function countLeaves(nodes: TreeNode[]): number {
  return nodes.reduce((n, node) => n + (node.children ? countLeaves(node.children) : 1), 0);
}
