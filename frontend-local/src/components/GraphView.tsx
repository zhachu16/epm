import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeChange,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AppState, MetricInfo } from "../api/types";
import { formatNumber, headline } from "../lib/format";
import { buildGraph, groupNodeId, neighbourhood, structureKey } from "../lib/graph";
import { type Layout, layoutGraph } from "../lib/layout";
import { useStore } from "../store";

type MetricData = { name: string; info: MetricInfo; selected: boolean; dim: boolean; match: boolean };
type GroupData = { group: string; count: number };
type MetricNode = Node<MetricData, "metric">;
type GroupNode = Node<GroupData, "metricGroup" | "collapsed">;

const groupLabel = (group: string) => {
  const text = group.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

function MetricNodeView({ data }: NodeProps<MetricNode>) {
  const { info, name } = data;
  const { label, value } = headline(info.value);
  const kind = info.kind === "formula+history" ? "history" : info.kind;
  const state = info.status?.state;
  return (
    <div
      className={`metric-node ${kind} ${data.selected ? "selected" : ""} ${data.dim ? "dim" : ""} ${data.match ? "match" : ""}`}
      data-testid={`node-${name}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="name">
        {state && state !== "ok" && <span className={`dot ${state}`} />}
        {name}
        {info.circularity && (
          <span className="loop" title={`solved iteratively by ${info.circularity}`}>
            ↻
          </span>
        )}
      </div>
      <div className="val">
        <span>{formatNumber(value, info.unit)}</span>
        {label && <span>({label})</span>}
        {info.unit && info.unit !== "%" && info.unit !== "x" && <span>{info.unit}</span>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function GroupNodeView({ data }: NodeProps<GroupNode>) {
  const toggle = useStore((s) => s.toggleGroup);
  return (
    <div className="group-node">
      <div className="group-title">
        <button className="icon-btn" onClick={() => toggle(data.group)} title="Collapse group" data-testid={`collapse-${data.group}`}>
          ▾
        </button>
        {groupLabel(data.group)}
        <span className="muted">· {data.count}</span>
      </div>
    </div>
  );
}

function CollapsedNodeView({ data }: NodeProps<GroupNode>) {
  const toggle = useStore((s) => s.toggleGroup);
  return (
    <div className="collapsed-node" onDoubleClick={() => toggle(data.group)} data-testid={`group-${data.group}`}>
      <Handle type="target" position={Position.Left} />
      <div className="group-title">
        <button className="icon-btn" onClick={() => toggle(data.group)} title="Expand group">
          ▸
        </button>
        {groupLabel(data.group)}
      </div>
      <div className="sub">{data.count} metrics</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

// "group" is a built-in React Flow type with its own styling, so use another name
const nodeTypes = { metric: MetricNodeView, metricGroup: GroupNodeView, collapsed: CollapsedNodeView };

/** Large models start as an overview of groups; small ones show every metric. */
const OVERVIEW_THRESHOLD = 40;

type Focus = "all" | "upstream" | "downstream" | "both";

interface Hover {
  name: string;
  x: number;
  y: number;
  edge?: { source: string; lagged: boolean; whole: boolean };
}

export function GraphView({ state }: { state: AppState }) {
  return (
    <ReactFlowProvider>
      <GraphCanvas state={state} />
    </ReactFlowProvider>
  );
}

function GraphCanvas({ state }: { state: AppState }) {
  const { collapsed, setCollapsed, selected, open, positions, moveNodes, resetLayout } = useStore();
  const initialised = useRef(false);
  if (!initialised.current) {
    initialised.current = true;
    const names = Object.keys(state.metrics);
    if (names.length > OVERVIEW_THRESHOLD && collapsed.length === 0 && !useStore.getState().graphInitialised) {
      const groups = [...new Set(names.map((n) => state.metrics[n].group))];
      useStore.setState({ collapsed: groups, graphInitialised: true });
    }
  }
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState<Focus>("all");
  const [layout, setLayout] = useState<Layout | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const flow = useReactFlow();

  const focusName = focus !== "all" && selected?.kind === "metric" && state.metrics[selected.name] ? selected.name : null;
  const only = useMemo(
    () => (focusName ? neighbourhood(state.metrics, focusName, focus as Exclude<Focus, "all">) : undefined),
    [state.metrics, focusName, focus],
  );
  const model = useMemo(() => buildGraph(state.metrics, collapsed, only), [state.metrics, collapsed, only]);
  const key = structureKey(model);

  useEffect(() => {
    let cancelled = false;
    layoutGraph(model).then((result) => {
      if (!cancelled) setLayout(result);
    });
    return () => {
      cancelled = true;
    };
    // the layout only depends on the structure, not on values
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // after a group is expanded, zoom to it; otherwise fit everything
  const expanded = useRef<string | null>(null);
  const previous = useRef<string[]>(collapsed);
  useEffect(() => {
    const opened = previous.current.filter((g) => !collapsed.includes(g));
    expanded.current = opened.length === 1 ? opened[0] : null;
    previous.current = collapsed;
  }, [collapsed]);
  useEffect(() => {
    if (!layout) return;
    requestAnimationFrame(() => {
      const group = expanded.current;
      if (group && layout.groups.has(groupNodeId(group))) {
        flow.fitView({ nodes: [{ id: groupNodeId(group) }], padding: 0.15, maxZoom: 1.2, duration: 300 });
      } else {
        flow.fitView({ padding: 0.05, maxZoom: 1.1, duration: 250 });
      }
    });
  }, [layout, flow]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    return new Set(Object.keys(state.metrics).filter((n) => n.toLowerCase().includes(q)));
  }, [query, state.metrics]);

  const nodes = useMemo<Node[]>(() => {
    if (!layout) return [];
    const out: Node[] = [];
    const visible = new Set(model.nodes.map((n) => n.id));
    for (const g of model.groups) {
      const id = groupNodeId(g.id);
      const box = layout.groups.get(id);
      if (!box) continue;
      const position = positions[id] ?? { x: box.x, y: box.y };
      if (g.collapsed) {
        out.push({ id, type: "collapsed", position, data: { group: g.id, count: g.members.length } });
        continue;
      }
      out.push({
        id,
        type: "metricGroup",
        position,
        style: { width: box.width, height: box.height },
        data: { group: g.id, count: g.members.length },
        selectable: false,
        dragHandle: ".group-title",
      });
      for (const name of g.members) {
        const nodeBox = layout.nodes.get(name);
        if (!visible.has(name) || !nodeBox) continue;
        out.push({
          id: name,
          type: "metric",
          parentId: id,
          position: positions[name] ?? { x: nodeBox.x, y: nodeBox.y },
          data: {
            name,
            info: state.metrics[name],
            selected: selected?.kind === "metric" && selected.name === name,
            dim: matches !== null && !matches.has(name),
            match: matches !== null && matches.has(name),
          },
        });
      }
    }
    return out;
  }, [layout, model, state.metrics, selected, matches, positions]);

  // Nodes are laid out automatically; dragging one moves it until the page is reloaded or the
  // layout is reset. A metric may leave its group's box; the group's title moves the group.
  const onNodesChange = (changes: NodeChange[]) => {
    const moved: Record<string, { x: number; y: number }> = {};
    for (const change of changes) {
      if (change.type === "position" && change.position) moved[change.id] = change.position;
    }
    if (Object.keys(moved).length) moveNodes(moved);
  };

  const edges = useMemo<Edge[]>(
    () =>
      model.edges.map((e) => {
        const touches = selected?.kind === "metric" && (e.source === selected.name || e.target === selected.name);
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.count > 1 ? String(e.count) : undefined,
          labelStyle: { fill: "var(--muted)", fontSize: 10 },
          labelBgStyle: { fill: "var(--panel)" },
          labelBgPadding: [3, 1] as [number, number],
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
          style: {
            strokeDasharray: e.lagged ? "6 4" : e.whole ? "2 3" : undefined,
            stroke: touches ? "var(--accent)" : undefined,
            strokeWidth: touches ? 2 : 1,
          },
          data: { lagged: e.lagged, whole: e.whole },
        };
      }),
    [model.edges, selected],
  );

  const point = (event: React.MouseEvent) => {
    const rect = wrapper.current?.getBoundingClientRect();
    return { x: event.clientX - (rect?.left ?? 0) + 14, y: event.clientY - (rect?.top ?? 0) + 14 };
  };

  const jumpToMatch = () => {
    const first = matches && [...matches][0];
    if (!first) return;
    const node = flow.getInternalNode(first);
    if (node) {
      const { x, y } = node.internals.positionAbsolute;
      flow.setCenter(x + 95, y + 24, { zoom: 1.2, duration: 300 });
    }
  };

  const hovered = hover ? state.metrics[hover.name] : null;
  const allGroups = model.groups.map((g) => g.id);

  return (
    <div className="graph" ref={wrapper} data-testid="graph">
      <div className="graph-toolbar">
        <input
          placeholder="Find metric…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && jumpToMatch()}
          data-testid="graph-search"
        />
        <select value={focus} onChange={(e) => setFocus(e.target.value as Focus)} title="Show only what the selected metric depends on, or what depends on it">
          <option value="all">All metrics</option>
          <option value="upstream" disabled={!selected || selected.kind !== "metric"}>
            Upstream of selection
          </option>
          <option value="downstream" disabled={!selected || selected.kind !== "metric"}>
            Downstream of selection
          </option>
          <option value="both" disabled={!selected || selected.kind !== "metric"}>
            Both directions
          </option>
        </select>
        <button className="btn small" onClick={() => setCollapsed(allGroups)}>
          Collapse all
        </button>
        <button className="btn small" onClick={() => setCollapsed([])}>
          Expand all
        </button>
        <button
          className="btn small"
          disabled={!Object.keys(positions).length}
          onClick={() => {
            resetLayout();
            requestAnimationFrame(() => flow.fitView({ padding: 0.05, maxZoom: 1.1, duration: 250 }));
          }}
          title="Put dragged nodes back where the automatic layout puts them"
          data-testid="reset-layout"
        >
          Reset layout
        </button>
        <span className="pill" title="Edge styles">
          <svg width="26" height="6" aria-hidden>
            <line x1="0" y1="3" x2="26" y2="3" stroke="currentColor" strokeDasharray="6 4" />
          </svg>
          previous period
          <svg width="26" height="6" aria-hidden>
            <line x1="0" y1="3" x2="26" y2="3" stroke="currentColor" strokeDasharray="2 3" />
          </svg>
          whole series
        </span>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        minZoom={0.05}
        maxZoom={2}
        nodesConnectable={false}
        onNodesChange={onNodesChange}
        onlyRenderVisibleElements
        onNodeClick={(_, node) => {
          if (node.type === "metric") open("metric", node.id);
        }}
        onNodeMouseEnter={(event, node) => {
          if (node.type === "metric") setHover({ name: node.id, ...point(event) });
        }}
        onNodeMouseLeave={() => setHover(null)}
        onEdgeMouseEnter={(event, edge) => {
          if (state.metrics[edge.target]) {
            const data = edge.data as { lagged: boolean; whole: boolean };
            setHover({ name: edge.target, ...point(event), edge: { source: edge.source, ...data } });
          }
        }}
        onEdgeMouseLeave={() => setHover(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {hover && hovered && (
        <div className="tooltip" style={{ left: hover.x, top: hover.y }} data-testid="graph-tooltip">
          {hover.edge && (
            <div className="t-meta">
              {hover.edge.source.replace(/^group:/, "")} → {hover.name}
              {hover.edge.lagged ? " · previous period" : hover.edge.whole ? " · whole series" : ""}
            </div>
          )}
          <div className="t-name">{hover.name}</div>
          <div className="t-formula">
            {hovered.formula ?? (hovered.kind === "input" ? "input from a source" : "input value")}
          </div>
          <div className="t-meta">
            {[hovered.unit, hovered.axis ? `axis ${hovered.axis}` : "scalar", hovered.scope === "forecast" ? "forecast only" : null]
              .filter(Boolean)
              .join(" · ")}
            {" · "}
            {formatNumber(headline(hovered.value).value, hovered.unit)}
          </div>
        </div>
      )}
    </div>
  );
}
