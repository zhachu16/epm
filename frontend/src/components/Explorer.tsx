import { useMemo, useState } from "react";
import type { AppState } from "../api/types";
import { buildTree, countLeaves, type TreeNode } from "../lib/tree";
import { type TabKind, useStore } from "../store";

const NEW_KINDS: [TabKind, string][] = [
  ["metric", "Metric"],
  ["table", "Table"],
  ["analysis", "Analysis"],
  ["check", "Check"],
  ["function", "Function"],
  ["source", "Source"],
  ["circularity", "Circularity"],
  ["axis", "Axis"],
];

/** Names already taken by objects of ``kind``. */
function existing(state: AppState, kind: TabKind): string[] {
  const m = state.model;
  if (kind === "function") return state.functions.map((f) => f.name);
  const collection = { metric: m.metrics, table: m.tables, analysis: m.analyses, check: m.checks, source: m.sources, circularity: m.circularities, axis: m.axes }[
    kind as string
  ];
  return Object.keys(collection ?? {});
}

export function Explorer({ state }: { state: AppState }) {
  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(state, query.trim()), [state, query]);
  const { open, selected, log } = useStore();

  const create = (kind: TabKind) => {
    const name = window.prompt(`Name of the new ${kind}:`)?.trim();
    if (!name) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      log("error", `'${name}' is not a valid name: use letters, digits and underscores`);
      return;
    }
    if (existing(state, kind).includes(name)) log("warning", `${kind} '${name}' already exists; opening it`);
    open(kind, name);
  };

  const toggle = (id: string) =>
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const render = (node: TreeNode, depth: number) => {
    const pad = { paddingLeft: 6 + depth * 12 };
    if (node.children) {
      const isOpen = query !== "" || !closed.has(node.id);
      return (
        <div key={node.id}>
          <div className={`tree-row ${depth === 0 ? "section" : ""}`} style={pad} onClick={() => toggle(node.id)}>
            <span className="chev">{isOpen ? "▾" : "▸"}</span>
            <span className="label">{node.label}</span>
            <span className="count">{countLeaves(node.children)}</span>
          </div>
          {isOpen && node.children.map((child) => render(child, depth + 1))}
        </div>
      );
    }
    const active = selected?.kind === node.kind && selected?.name === node.name;
    return (
      <div
        key={node.id}
        className={`tree-row ${active ? "active" : ""}`}
        style={pad}
        title={node.hint}
        onClick={() => node.kind && node.name && open(node.kind, node.name)}
        data-testid={`explorer-${node.id}`}
      >
        <span className="chev" />
        <span className="leaf-label">{node.label}</span>
        {node.badge && <span className={`dot ${node.badge}`} style={{ marginLeft: "auto" }} />}
      </div>
    );
  };

  return (
    <div className="panel">
      <div className="panel-header">
        Explorer
        <select
          className="new-object"
          value=""
          onChange={(e) => e.target.value && create(e.target.value as TabKind)}
          title="Create a new object"
          data-testid="explorer-new"
        >
          <option value="">+ New</option>
          {NEW_KINDS.map(([kind, label]) => (
            <option key={kind} value={kind}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="search">
        <input
          placeholder="Search metrics, tables, formulas…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="explorer-search"
        />
      </div>
      <div className="panel-body tree">{tree.length ? tree.map((n) => render(n, 0)) : <div className="empty">No matches</div>}</div>
    </div>
  );
}
