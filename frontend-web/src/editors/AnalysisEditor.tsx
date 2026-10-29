import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type { AnalysisDef, AppState, SweepResult } from "../api/types";
import { Chart } from "../components/Chart";
import { heatmapData, heatmapOption, lineData, lineOption } from "../lib/charts";
import { formatNumber, formatSpec } from "../lib/format";
import { useStore } from "../store";
import { ActionBar, errorsOf, RenameDelete, useDraft, useUpsert } from "./common";

interface InputRow {
  metric: string;
  kind: "values" | "range";
  values: string;
  start: string;
  stop: string;
  step: string;
}

interface Form {
  inputs: InputRow[];
  outputs: string[];
  mode: "grid" | "zip";
  overrides: string;
  plot: "" | "line" | "heatmap";
  x: string;
  y: string[];
  yInput: string;
  z: string;
  format: string;
  description: string;
}

/** The spacing of a range given by ``step`` or by a count of values (``num``). */
const stepOf = (r: { start: number; stop: number; step?: number; num?: number }) =>
  r.step ?? (r.num && r.num > 1 ? Number(((r.stop - r.start) / (r.num - 1)).toPrecision(12)) : "");

function toForm(def?: AnalysisDef): Form {
  const plot = def?.plot;
  return {
    inputs: (def?.inputs ?? []).map((i) => ({
      metric: i.metric,
      kind: i.range ? "range" : "values",
      values: (i.values ?? []).join(", "),
      start: i.range ? String(i.range.start) : "",
      stop: i.range ? String(i.range.stop) : "",
      step: i.range ? String(stepOf(i.range)) : "",
    })),
    outputs: def?.outputs ?? [],
    mode: def?.mode ?? "grid",
    overrides: Object.entries(def?.overrides ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
    plot: plot?.kind ?? "",
    x: plot?.x ?? "",
    y: plot?.kind === "line" ? (typeof plot.y === "string" ? [plot.y] : plot.y ?? []) : [],
    yInput: plot?.kind === "heatmap" && typeof plot.y === "string" ? plot.y : "",
    z: plot?.z ?? "",
    format: plot?.format ?? "",
    description: def?.description ?? "",
  };
}

const numbers = (text: string) =>
  text
    .split(/[,\s]+/)
    .filter(Boolean)
    .map(Number);

function expand(row: InputRow): number[] {
  if (row.kind === "values") return numbers(row.values);
  const [start, stop, step] = [Number(row.start), Number(row.stop), Number(row.step)];
  if (!step || !Number.isFinite(start) || !Number.isFinite(stop)) return [];
  const n = Math.round((stop - start) / step + 1e-9);
  return Array.from({ length: Math.max(n + 1, 0) }, (_, i) => Number((start + i * step).toPrecision(12)));
}

function parseOverrides(text: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const line of text.split(/\n|,/)) {
    const [k, v] = line.split("=").map((s) => s.trim());
    if (k && v !== undefined && v !== "") out[k] = Number(v);
  }
  return out;
}

function toDef(form: Form): Record<string, unknown> {
  const inputs = form.inputs.map((i) =>
    i.kind === "range"
      ? { metric: i.metric, range: { start: Number(i.start), stop: Number(i.stop), step: Number(i.step) } }
      : { metric: i.metric, values: numbers(i.values) },
  );
  let plot: Record<string, unknown> | null = null;
  if (form.plot === "line") plot = { kind: "line", x: form.x || inputs[0]?.metric, ...(form.y.length ? { y: form.y } : {}) };
  if (form.plot === "heatmap") plot = { kind: "heatmap", x: form.x || inputs[0]?.metric, y: form.yInput || inputs[1]?.metric, z: form.z || form.outputs[0] };
  if (plot && form.format) plot.format = form.format;
  const overrides = parseOverrides(form.overrides);
  return {
    inputs,
    outputs: form.outputs,
    mode: form.mode,
    overrides: Object.keys(overrides).length ? overrides : null,
    plot,
    description: form.description.trim() || null,
  };
}

export function AnalysisEditor({ state, name }: { state: AppState; name: string }) {
  const definition = state.model.analyses?.[name];
  const source = useMemo(() => toForm(definition), [definition]);
  const { draft: form, setDraft: setForm, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const [result, setResult] = useState<SweepResult | null>(null);
  const [running, setRunning] = useState(false);
  const [newOutput, setNewOutput] = useState("");
  const upsert = useUpsert("analysis", name);
  const { log, readOnly, setBottom } = useStore();
  const client = useQueryClient();
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const metrics = Object.keys(state.metrics);
  const inputNames = form.inputs.map((i) => i.metric).filter(Boolean);

  const run = async () => {
    setRunning(true);
    setErrors([]);
    try {
      const inputs = Object.fromEntries(form.inputs.map((i) => [i.metric, expand(i)]));
      const sweep = await api.sweep(inputs, form.outputs, form.mode, parseOverrides(form.overrides));
      setResult(sweep);
      log("info", `analysis ${name}: ${sweep.rows.length} scenarios`);
    } catch (e) {
      setErrors([e instanceof ApiError ? e.message : String(e)]);
    } finally {
      setRunning(false);
    }
  };

  /** Run the saved analysis and keep its results (and chart) as downloadable outputs. */
  const saveResults = async () => {
    setSaving(true);
    setErrors([]);
    try {
      const r = await api.runAnalysis(name, { save: true, plot: Boolean(definition?.plot) });
      setResult(r.sweep);
      log("info", `analysis ${name}: saved ${r.outputs?.map((o) => o.name).join(", ")}`);
      await client.invalidateQueries({ queryKey: ["outputs"] });
      setBottom("outputs");
    } catch (e) {
      setErrors([e instanceof ApiError ? e.message : String(e)]);
    } finally {
      setSaving(false);
    }
  };

  const submit = async (mode: "commit" | "dry_run") => {
    expectSave(mode === "commit");
    const r = await upsert(toDef(form), mode);
    if (!r.committed) expectSave(false);
    setErrors(errorsOf(r));
    return r;
  };

  const addOutput = (metric: string) => {
    if (!metric) return;
    const info = state.metrics[metric];
    const periods = info?.axis ? state.model.axes?.[info.axis]?.values ?? [] : [];
    const selector = periods.length ? `${metric}@${periods[periods.length - 1]}` : metric;
    if (!form.outputs.includes(selector)) set("outputs", [...form.outputs, selector]);
    setNewOutput("");
  };

  // The chart, and the outputs it plots: the plot's format applies only to those.
  const chart = useMemo(() => {
    if (!result || !result.rows.length) return null;
    const kind = form.plot || (result.inputs.length === 2 ? "heatmap" : "line");
    const x = form.x || result.inputs[0];
    if (kind === "heatmap" && result.inputs.length >= 2) {
      const y = form.yInput || result.inputs.find((i) => i !== x)!;
      const z = form.z || result.outputs[0];
      return { option: heatmapOption(heatmapData(result, x, y, z), x, y, z, form.format), plotted: [z] };
    }
    const ys = form.y.length ? form.y : result.outputs;
    return { option: lineOption(lineData(result, x, ys), x, form.format), plotted: ys };
  }, [result, form.plot, form.x, form.yInput, form.z, form.y, form.format]);

  const unitOf = (selector: string) => state.metrics[selector.split("@")[0]]?.unit;
  const scenarioCount = form.mode === "zip" ? Math.min(...form.inputs.map((i) => expand(i).length)) : form.inputs.reduce((n, i) => n * expand(i).length, 1);

  return (
    <div className="editor" style={{ maxWidth: "none" }} data-testid="analysis-editor">
      <div className="editor-header">
        <h1>{name}</h1>
        <span className="pill">analysis</span>
        {!definition && <span className="pill">new</span>}
        <div className="actions">
          <RenameDelete kind="analysis" tabKind="analysis" name={name} exists={Boolean(definition)} />
        </div>
      </div>
      {form.description && <div className="muted">{form.description}</div>}

      <div className="card">
        <div className="card-title">Inputs to vary</div>
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {form.inputs.map((row, i) => {
            const update = (patch: Partial<InputRow>) => set("inputs", form.inputs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
            return (
              <div key={i} className="row">
                <select className="input" style={{ width: 200 }} value={row.metric} onChange={(e) => update({ metric: e.target.value })}>
                  <option value="">metric…</option>
                  {metrics.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
                <div className="segmented">
                  <button className={row.kind === "values" ? "on" : ""} onClick={() => update({ kind: "values" })}>
                    values
                  </button>
                  <button className={row.kind === "range" ? "on" : ""} onClick={() => update({ kind: "range" })}>
                    range
                  </button>
                </div>
                {row.kind === "values" ? (
                  <input className="input" style={{ width: 280 }} placeholder="0.04, 0.06, 0.08" value={row.values} onChange={(e) => update({ values: e.target.value })} />
                ) : (
                  <>
                    <input className="input" style={{ width: 90 }} placeholder="start" value={row.start} onChange={(e) => update({ start: e.target.value })} />
                    <input className="input" style={{ width: 90 }} placeholder="stop" value={row.stop} onChange={(e) => update({ stop: e.target.value })} />
                    <input className="input" style={{ width: 90 }} placeholder="step" value={row.step} onChange={(e) => update({ step: e.target.value })} />
                  </>
                )}
                <span className="muted">{expand(row).length} values · base {formatNumber(state.metrics[row.metric]?.value?.value, state.metrics[row.metric]?.unit)}</span>
                <button className="icon-btn" onClick={() => set("inputs", form.inputs.filter((_, j) => j !== i))}>
                  ×
                </button>
              </div>
            );
          })}
          <div className="row">
            <button className="btn small" onClick={() => set("inputs", [...form.inputs, { metric: "", kind: "values", values: "", start: "", stop: "", step: "" }])}>
              Add input
            </button>
            <div className="segmented">
              <button className={form.mode === "grid" ? "on" : ""} onClick={() => set("mode", "grid")} title="Every combination of input values">
                all combinations
              </button>
              <button className={form.mode === "zip" ? "on" : ""} onClick={() => set("mode", "zip")} title="Pair values position by position">
                paired
              </button>
            </div>
            <span className="muted">{Number.isFinite(scenarioCount) ? scenarioCount : 0} scenarios</span>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Outputs</div>
        <div className="card-body row">
          {form.outputs.map((o) => (
            <span key={o} className="link">
              {o}
              <span className="icon-btn" onClick={() => set("outputs", form.outputs.filter((x) => x !== o))}>
                ×
              </span>
            </span>
          ))}
          <select className="input" style={{ width: 220 }} value={newOutput} onChange={(e) => addOutput(e.target.value)}>
            <option value="">add output…</option>
            {metrics.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
          <span className="muted">series outputs use one period, e.g. debt@2029</span>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Plot and fixed assumptions</div>
        <div className="card-body form-grid">
          <label>Chart</label>
          <div className="row">
            <select className="input" style={{ width: 130 }} value={form.plot} onChange={(e) => set("plot", e.target.value as Form["plot"])}>
              <option value="">automatic</option>
              <option value="line">line</option>
              <option value="heatmap">heatmap</option>
            </select>
            <select className="input" style={{ width: 170 }} value={form.x} onChange={(e) => set("x", e.target.value)} title="x axis">
              <option value="">x: first input</option>
              {inputNames.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
            {form.plot === "heatmap" && (
              <>
                <select className="input" style={{ width: 170 }} value={form.yInput} onChange={(e) => set("yInput", e.target.value)} title="y axis">
                  <option value="">y: second input</option>
                  {inputNames.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
                <select className="input" style={{ width: 170 }} value={form.z} onChange={(e) => set("z", e.target.value)} title="colour">
                  <option value="">colour: first output</option>
                  {form.outputs.map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </select>
              </>
            )}
            <input className="input" style={{ width: 110 }} placeholder="format .1%" value={form.format} onChange={(e) => set("format", e.target.value)} />
          </div>
          <label>Fixed assumptions</label>
          <textarea placeholder="one NAME=VALUE per line, applied to every scenario" value={form.overrides} onChange={(e) => set("overrides", e.target.value)} />
        </div>
      </div>

      <ActionBar dirty={dirty || !definition} onSubmit={submit} onRevert={revert} errors={errors}>
        <button className="btn" onClick={run} disabled={running || !form.inputs.length || !form.outputs.length} data-testid="run-analysis">
          {running ? "Running…" : "Run"}
        </button>
        {definition && !dirty && !readOnly && (
          <button className="btn" onClick={saveResults} disabled={saving} title="Run the saved analysis and keep its results and chart as outputs" data-testid="save-results">
            {saving ? "Saving…" : "Save results"}
          </button>
        )}
        {definition?.plot && !dirty && (
          <a className="btn" href={api.plotUrl(name)} target="_blank" rel="noreferrer" data-testid="plot-png">
            Chart PNG
          </a>
        )}
      </ActionBar>

      {result && (
        <div className="card">
          <div className="card-title">
            Results <span className="muted">{result.rows.length} scenarios · base assumptions unchanged</span>
          </div>
          <div className="card-body">{chart && <Chart option={chart.option} />}</div>
          <div className="card-body" style={{ overflowX: "auto", maxHeight: 320 }}>
            <table className="grid" data-testid="analysis-results">
              <thead>
                <tr>
                  {[...result.inputs, ...result.outputs, "status"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, i) => (
                  <tr key={i}>
                    {result.inputs.map((k) => (
                      <td key={k}>{formatNumber(row.inputs[k], state.metrics[k]?.unit)}</td>
                    ))}
                    {result.outputs.map((k) => (
                      <td key={k}>
                        {form.format && chart?.plotted.includes(k) ? formatSpec(row.outputs[k], form.format) : formatNumber(row.outputs[k], unitOf(k))}
                      </td>
                    ))}
                    <td title={row.error ?? undefined} className={row.status === "ok" ? "" : "missing"}>
                      {row.status}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
