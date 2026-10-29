import { describe, expect, it } from "vitest";
import type { MetricInfo } from "../api/types";
import { buildGraph, groupNodeId, neighbourhood } from "./graph";
import { formatNumber, formatSpec, headline } from "./format";

function metric(group: string, deps: Record<string, number | null>, used_by: string[] = []): MetricInfo {
  return {
    kind: "formula",
    axis: null,
    unit: null,
    scope: "all",
    folder: null,
    description: null,
    formula: null,
    deps,
    used_by,
    status: null,
    value: null,
    fingerprint: null,
    circularity: null,
    group,
  };
}

const metrics: Record<string, MetricInfo> = {
  a: metric("inputs", {}, ["b", "c"]),
  b: metric("is", { a: 0 }, ["c", "d"]),
  c: metric("is", { a: 1, b: 0 }, ["d"]),
  d: metric("returns", { b: null, c: 0 }),
};

describe("buildGraph", () => {
  it("draws metrics and dependency edges", () => {
    const g = buildGraph(metrics);
    expect(g.nodes.map((n) => n.id)).toEqual(["a", "b", "c", "d"]);
    expect(g.edges.map((e) => e.id).sort()).toEqual(["a->b", "a->c", "b->c", "b->d", "c->d"]);
    expect(g.edges.find((e) => e.id === "a->c")?.lagged).toBe(true);
    expect(g.edges.find((e) => e.id === "b->d")?.whole).toBe(true);
  });

  it("merges edges into collapsed groups", () => {
    const g = buildGraph(metrics, ["is"]);
    expect(g.nodes.map((n) => n.id)).toEqual(["a", "d"]);
    const is = groupNodeId("is");
    expect(g.edges.map((e) => [e.id, e.count])).toEqual([
      [`a->${is}`, 2],
      [`${is}->d`, 2],
    ]);
    expect(g.groups.find((x) => x.id === "is")?.collapsed).toBe(true);
  });

  it("finds neighbourhoods", () => {
    expect([...neighbourhood(metrics, "c", "upstream")].sort()).toEqual(["a", "b", "c"]);
    expect([...neighbourhood(metrics, "b", "downstream")].sort()).toEqual(["b", "c", "d"]);
    const only = neighbourhood(metrics, "d", "upstream");
    expect(buildGraph(metrics, [], new Set(["c", "d"])).edges.map((e) => e.id)).toEqual(["c->d"]);
    expect(only.size).toBe(4);
  });
});

describe("format", () => {
  it("formats by unit", () => {
    expect(formatNumber(0.1735, "%")).toBe("17.4%");
    expect(formatNumber(2.2251, "x")).toBe("2.23x");
    expect(formatNumber(2070.04)).toBe("2,070.0");
    expect(formatNumber(null)).toBe("—");
    expect(formatSpec(0.1735, ".1%")).toBe("17.4%");
    expect(formatSpec(1234.5, ",.0f")).toBe("1,235");
  });

  it("picks the headline value", () => {
    expect(headline({ axis: null, value: 3 })).toEqual({ label: null, value: 3 });
    expect(headline({ axis: "fy", values: { "2024": 1, "2025": 2, "2026": null } })).toEqual({ label: "2025", value: 2 });
  });
});
