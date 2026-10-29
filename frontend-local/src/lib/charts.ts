import type { SweepResult } from "../api/types";
import { formatSpec } from "./format";

export interface LineData {
  xs: number[];
  series: { name: string; data: (number | null)[] }[];
}

export interface HeatmapData {
  xs: number[];
  ys: number[];
  values: (number | null)[][]; // rows follow ys, columns follow xs
}

const unique = (values: number[]) => [...new Set(values)];

/** One line per output, and per combination of the other inputs in a grid. */
export function lineData(sweep: SweepResult, x: string, ys: string[]): LineData {
  const others = sweep.inputs.filter((i) => i !== x);
  const xs = unique(sweep.rows.map((r) => r.inputs[x]));
  const groups = new Map<string, Map<number, SweepResult["rows"][number]>>();
  for (const row of sweep.rows) {
    const key = others.map((o) => `${o}=${row.inputs[o]}`).join(", ");
    if (!groups.has(key)) groups.set(key, new Map());
    groups.get(key)!.set(row.inputs[x], row);
  }
  const series: LineData["series"] = [];
  for (const y of ys) {
    for (const [key, byX] of groups) {
      series.push({
        name: key ? `${y} (${key})` : y,
        data: xs.map((xv) => byX.get(xv)?.outputs[y] ?? null),
      });
    }
  }
  return { xs, series };
}

export function heatmapData(sweep: SweepResult, x: string, y: string, z: string): HeatmapData {
  const xs = unique(sweep.rows.map((r) => r.inputs[x]));
  const ys = unique(sweep.rows.map((r) => r.inputs[y]));
  const lookup = new Map(sweep.rows.map((r) => [`${r.inputs[x]}|${r.inputs[y]}`, r.outputs[z] ?? null]));
  return { xs, ys, values: ys.map((yv) => xs.map((xv) => lookup.get(`${xv}|${yv}`) ?? null)) };
}

const axisLabel = (v: number) => String(Number(v.toPrecision(8)));

export function lineOption(data: LineData, x: string, format?: string | null) {
  return {
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) => formatSpec(v, format),
    },
    legend: { top: 0, type: "scroll" },
    grid: { left: 64, right: 24, top: 36, bottom: 44 },
    xAxis: { type: "category", data: data.xs.map(axisLabel), name: x, nameLocation: "middle", nameGap: 28 },
    yAxis: { type: "value", scale: true, axisLabel: { formatter: (v: number) => formatSpec(v, format) } },
    series: data.series.map((s) => ({ name: s.name, type: "line", data: s.data, connectNulls: false, symbolSize: 6 })),
  };
}

export function heatmapOption(data: HeatmapData, x: string, y: string, z: string, format?: string | null) {
  const cells: [number, number, number | null][] = [];
  data.values.forEach((row, yi) => row.forEach((v, xi) => cells.push([xi, yi, v])));
  const finite = cells.map((c) => c[2]).filter((v): v is number => v !== null);
  return {
    tooltip: {
      formatter: (p: { value: [number, number, number | null] }) =>
        `${x} ${axisLabel(data.xs[p.value[0]])}, ${y} ${axisLabel(data.ys[p.value[1]])}<br/><b>${z}: ${formatSpec(p.value[2], format)}</b>`,
    },
    grid: { left: 64, right: 96, top: 16, bottom: 48 },
    xAxis: { type: "category", data: data.xs.map(axisLabel), name: x, nameLocation: "middle", nameGap: 30, splitArea: { show: true } },
    yAxis: { type: "category", data: data.ys.map(axisLabel), name: y, nameLocation: "middle", nameGap: 44, splitArea: { show: true } },
    visualMap: {
      min: finite.length ? Math.min(...finite) : 0,
      max: finite.length ? Math.max(...finite) : 1,
      calculable: true,
      orient: "vertical",
      right: 8,
      top: "middle",
      formatter: (v: number) => formatSpec(v, format),
    },
    series: [
      {
        name: z,
        type: "heatmap",
        data: cells,
        label: { show: true, formatter: (p: { value: [number, number, number | null] }) => formatSpec(p.value[2], format) },
      },
    ],
  };
}
