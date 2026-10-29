import ELK, { type ElkNode } from "elkjs/lib/elk.bundled.js";
import { type GraphModel, groupNodeId } from "./graph";

const elk = new ELK();

export const NODE_WIDTH = 190;
export const NODE_HEIGHT = 48;
const COLLAPSED = { width: 210, height: 58 };

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Layout {
  groups: Map<string, Box>; // absolute
  nodes: Map<string, Box>; // relative to their group
}

/** Layered left-to-right layout; groups are compound nodes laid out together. */
export async function layoutGraph(model: GraphModel): Promise<Layout> {
  const visible = new Set(model.nodes.map((n) => n.id));
  const children: ElkNode[] = [];
  for (const g of model.groups) {
    if (g.collapsed) {
      children.push({ id: groupNodeId(g.id), ...COLLAPSED });
      continue;
    }
    const members = g.members.filter((m) => visible.has(m));
    if (!members.length) continue;
    children.push({
      id: groupNodeId(g.id),
      layoutOptions: { "elk.padding": "[top=38,left=14,bottom=14,right=14]" },
      children: members.map((id) => ({ id, width: NODE_WIDTH, height: NODE_HEIGHT })),
    });
  }
  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "44",
      "elk.spacing.nodeNode": "14",
      "elk.layered.spacing.edgeNodeBetweenLayers": "20",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    },
    children,
    edges: model.edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
  const result = await elk.layout(graph);
  const layout: Layout = { groups: new Map(), nodes: new Map() };
  for (const group of result.children ?? []) {
    layout.groups.set(group.id, {
      x: group.x ?? 0,
      y: group.y ?? 0,
      width: group.width ?? COLLAPSED.width,
      height: group.height ?? COLLAPSED.height,
    });
    for (const node of group.children ?? []) {
      layout.nodes.set(node.id, { x: node.x ?? 0, y: node.y ?? 0, width: NODE_WIDTH, height: NODE_HEIGHT });
    }
  }
  return layout;
}
