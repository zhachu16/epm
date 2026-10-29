import { describe, expect, it } from "vitest";
import type { SweepResult } from "../api/types";
import { heatmapData, lineData } from "./charts";

const sweep: SweepResult = {
  inputs: ["a", "b"],
  outputs: ["irr"],
  mode: "grid",
  rows: [
    { inputs: { a: 1, b: 10 }, outputs: { irr: 0.1 }, status: "ok", error: null },
    { inputs: { a: 1, b: 20 }, outputs: { irr: 0.2 }, status: "ok", error: null },
    { inputs: { a: 2, b: 10 }, outputs: { irr: 0.3 }, status: "ok", error: null },
    { inputs: { a: 2, b: 20 }, outputs: { irr: null }, status: "error", error: "boom" },
  ],
};

describe("charts", () => {
  it("draws one line per output and other-input combination", () => {
    const data = lineData(sweep, "a", ["irr"]);
    expect(data.xs).toEqual([1, 2]);
    expect(data.series).toEqual([
      { name: "irr (b=10)", data: [0.1, 0.3] },
      { name: "irr (b=20)", data: [0.2, null] },
    ]);
  });

  it("lays out heatmap cells by y rows and x columns", () => {
    expect(heatmapData(sweep, "a", "b", "irr")).toEqual({
      xs: [1, 2],
      ys: [10, 20],
      values: [
        [0.1, 0.3],
        [0.2, null],
      ],
    });
  });
});
