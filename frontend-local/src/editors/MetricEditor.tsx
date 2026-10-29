import { useMemo, useState } from "react";
import type { AppState, MetricDef } from "../api/types";
import { ValuesTable } from "../components/ValuesTable";
import { useStore } from "../store";
import { ActionBar, errorsOf, RenameDelete, useDraft, useUpsert } from "./common";
import { FormulaInput } from "./FormulaInput";

type DefMode = "formula" | "value" | "values" | "input";

interface Form {
  mode: DefMode;
  formula: string;
  value: string;
  values: Record<string, string>;
  inputSource: string;
  inputTarget: "item" | "cell";
  inputRef: string;
  scale: string;
  useHistory: boolean;
  historySource: string;
  historyItem: string;
  unit: string;
  axis: string;
  scope: "" | "all" | "forecast";
  folder: string;
  description: string;
}

function toForm(def: MetricDef | undefined): Form {
  const value = def?.value;
  const binding = def?.input ?? def?.history;
  return {
    mode: def?.input ? "input" : typeof value === "number" ? "value" : value ? "values" : "formula",
    formula: def?.formula ?? "",
    value: typeof value === "number" ? String(value) : "",
    values: value && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, v === null ? "" : String(v)])) : {},
    inputSource: def?.input?.source ?? "",
    inputTarget: def?.input?.cell ? "cell" : "item",
    inputRef: def?.input?.item ?? def?.input?.cell ?? "",
    scale: binding?.scale !== undefined ? String(binding.scale) : "",
    useHistory: Boolean(def?.history),
    historySource: def?.history?.source ?? "",
    historyItem: def?.history?.item ?? "",
    unit: def?.unit ?? "",
    axis: def?.axis ?? "",
    scope: def?.scope ?? "",
    folder: def?.folder ?? "",
    description: def?.description ?? "",
  };
}

const optional = (v: string) => (v.trim() === "" ? null : v.trim());

function toFields(form: Form): Record<string, unknown> {
  const fields: Record<string, unknown> = {
    formula: null,
    value: null,
    input: null,
    history: null,
    unit: optional(form.unit),
    axis: optional(form.axis),
    scope: form.scope || null,
    folder: optional(form.folder),
    description: optional(form.description),
  };
  const scale = form.scale.trim() === "" ? undefined : Number(form.scale);
  if (form.mode === "formula") {
    fields.formula = form.formula.trim();
    if (form.useHistory && form.historySource) {
      fields.history = { source: form.historySource, item: form.historyItem, ...(scale !== undefined ? { scale } : {}) };
    }
  } else if (form.mode === "value") {
    fields.value = Number(form.value);
  } else if (form.mode === "values") {
    fields.value = Object.fromEntries(
      Object.entries(form.values)
        .filter(([, v]) => v.trim() !== "")
        .map(([k, v]) => [k, Number(v)]),
    );
  } else {
    fields.input = {
      source: form.inputSource,
      [form.inputTarget]: form.inputRef,
      ...(scale !== undefined ? { scale } : {}),
    };
  }
  return fields;
}

export function MetricEditor({ state, name }: { state: AppState; name: string }) {
  const definition = state.model.metrics?.[name];
  const info = state.metrics[name];
  const source = useMemo(() => toForm(definition), [definition]);
  const { draft: form, setDraft: setForm, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const upsert = useUpsert("metric", name);
  const open = useStore((s) => s.open);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const axes = Object.keys(state.model.axes ?? {});
  const sources = Object.keys(state.model.sources ?? {});
  const axisName = form.axis || info?.axis || state.model.settings?.default_axis || axes[0];
  const periods = (axisName && state.model.axes?.[axisName]?.values.map(String)) || [];

  const submit = async (mode: "commit" | "dry_run" | "propose") => {
    expectSave(mode === "commit");
    const result = await upsert(toFields(form), mode);
    if (!result.committed) expectSave(false);
    setErrors(errorsOf(result));
    return result;
  };

  return (
    <div className="editor" data-testid="metric-editor">
      <div className="editor-header">
        <h1>{name}</h1>
        {!definition && <span className="pill">new</span>}
        {info?.status && info.status.state !== "ok" && (
          <span className={`pill ${info.status.state === "warning" ? "warn" : "error"}`}>{info.status.state}</span>
        )}
        <div className="actions">
          <RenameDelete kind="metric" tabKind="metric" name={name} exists={Boolean(definition)} />
        </div>
      </div>

      <div className="form-grid">
        <label>Defined by</label>
        <div className="segmented">
          {(
            [
              ["formula", "Formula"],
              ["value", "Value"],
              ["values", "Per period"],
              ["input", "Source"],
            ] as [DefMode, string][]
          ).map(([mode, label]) => (
            <button key={mode} className={form.mode === mode ? "on" : ""} onClick={() => set("mode", mode)} data-testid={`mode-${mode}`}>
              {label}
            </button>
          ))}
        </div>

        {form.mode === "formula" && (
          <>
            <label>Formula</label>
            <FormulaInput value={form.formula} onChange={(v) => set("formula", v)} onSubmit={() => void submit("commit")} testId="metric-formula" />
            <label>History</label>
            <div className="row">
              <label className="row">
                <input type="checkbox" checked={form.useHistory} onChange={(e) => set("useHistory", e.target.checked)} />
                historical periods from a source
              </label>
              {form.useHistory && (
                <>
                  <select className="input" style={{ width: 140 }} value={form.historySource} onChange={(e) => set("historySource", e.target.value)}>
                    <option value="">source…</option>
                    {sources.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                  <input className="input" style={{ width: 220 }} placeholder="row or column label" value={form.historyItem} onChange={(e) => set("historyItem", e.target.value)} />
                </>
              )}
            </div>
          </>
        )}

        {form.mode === "value" && (
          <>
            <label>Value</label>
            <input type="number" step="any" value={form.value} onChange={(e) => set("value", e.target.value)} data-testid="metric-value" />
          </>
        )}

        {form.mode === "values" && (
          <>
            <label>Values</label>
            <div className="row">
              {periods.map((p) => (
                <label key={p} style={{ display: "flex", flexDirection: "column", gap: 2, width: 92 }}>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {p}
                  </span>
                  <input
                    className="input"
                    type="number"
                    step="any"
                    value={form.values[p] ?? ""}
                    onChange={(e) => set("values", { ...form.values, [p]: e.target.value })}
                  />
                </label>
              ))}
            </div>
          </>
        )}

        {form.mode === "input" && (
          <>
            <label>Source</label>
            <div className="row">
              <select className="input" style={{ width: 140 }} value={form.inputSource} onChange={(e) => set("inputSource", e.target.value)}>
                <option value="">source…</option>
                {sources.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              <select className="input" style={{ width: 110 }} value={form.inputTarget} onChange={(e) => set("inputTarget", e.target.value as Form["inputTarget"])}>
                <option value="item">series</option>
                <option value="cell">one cell</option>
              </select>
              <input
                className="input"
                style={{ width: 220 }}
                placeholder={form.inputTarget === "cell" ? "B4 or Sheet!B4" : "row or column label"}
                value={form.inputRef}
                onChange={(e) => set("inputRef", e.target.value)}
              />
            </div>
          </>
        )}

        {(form.mode === "input" || (form.mode === "formula" && form.useHistory)) && (
          <>
            <label>Scale</label>
            <input type="number" step="any" placeholder="1" value={form.scale} onChange={(e) => set("scale", e.target.value)} />
          </>
        )}

        <label>Unit</label>
        <input type="text" placeholder={info?.unit ? `inferred: ${info.unit}` : "e.g. USD m, %, x"} value={form.unit} onChange={(e) => set("unit", e.target.value)} />
        <label>Axis</label>
        <select value={form.axis} onChange={(e) => set("axis", e.target.value)}>
          <option value="">{info ? `inferred (${info.axis ?? "scalar"})` : "inferred"}</option>
          {axes.map((a) => (
            <option key={a}>{a}</option>
          ))}
        </select>
        <label>Scope</label>
        <select value={form.scope} onChange={(e) => set("scope", e.target.value as Form["scope"])}>
          <option value="">{info ? `inferred (${info.scope})` : "inferred"}</option>
          <option value="all">all periods</option>
          <option value="forecast">forecast only</option>
        </select>
        <label>Folder</label>
        <input type="text" value={form.folder} onChange={(e) => set("folder", e.target.value)} />
        <label>Description</label>
        <input type="text" value={form.description} onChange={(e) => set("description", e.target.value)} />
      </div>

      <ActionBar dirty={dirty || !definition} onSubmit={submit} onRevert={revert} errors={errors} />

      {info && (
        <div className="card">
          <div className="card-title">
            Calculated values
            {info.status?.message && <span className="muted">{info.status.message}</span>}
          </div>
          <div className="card-body" style={{ overflowX: "auto" }}>
            <ValuesTable state={state} rows={[{ key: name, label: name, unit: info.unit, value: info.value }]} />
          </div>
          {info.used_by.length > 0 && (
            <div className="card-body">
              <span className="muted">Used by </span>
              <span className="links" style={{ display: "inline-flex" }}>
                {info.used_by.map((u) => (
                  <span key={u} className="link" onClick={() => open("metric", u)}>
                    {u}
                  </span>
                ))}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
