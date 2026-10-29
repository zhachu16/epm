import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { AppState, Op, SourceDef } from "../api/types";
import { formatNumber } from "../lib/format";
import { ActionBar, errorsOf, RenameDelete, useDraft, useUpsert } from "./common";

interface Form {
  file: string;
  sheet: string;
  range: string;
  orientation: "rows" | "columns";
  index: string;
  labels: string;
  axis: string;
  description: string;
}

const toForm = (def?: SourceDef): Form => ({
  file: def?.file ?? "",
  sheet: def?.sheet ?? "",
  range: def?.range ?? "",
  orientation: def?.orientation ?? "rows",
  index: def?.index !== undefined ? String(def.index) : "",
  labels: def?.labels !== undefined ? String(def.labels) : "",
  axis: def?.axis ?? "",
  description: def?.description ?? "",
});

const optional = (v: string) => (v.trim() === "" ? null : v.trim());
const lineRef = (v: string) => (v.trim() === "" ? null : /^\d+$/.test(v.trim()) ? Number(v) : v.trim().toUpperCase());

async function readFile(file: File): Promise<string> {
  const buffer = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < buffer.length; i += 0x8000) binary += String.fromCharCode(...buffer.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function SourceEditor({ state, name }: { state: AppState; name: string }) {
  const definition = state.model.sources?.[name];
  const source = useMemo(() => toForm(definition), [definition]);
  const { draft: form, setDraft: setForm, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const [upload, setUpload] = useState<{ path: string; data: string } | null>(null);
  const upsert = useUpsert("source", name);
  const preview = useQuery({
    queryKey: ["source-preview", name, state.version],
    queryFn: () => api.sourcePreview(name),
    enabled: Boolean(definition),
    retry: false,
  });
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const isRows = form.orientation === "rows";

  const submit = async (mode: "commit" | "dry_run") => {
    const extra: Op[] = upload ? [{ op: "upsert", kind: "file", name: upload.path, data: upload.data }] : [];
    expectSave(mode === "commit");
    const result = await upsert(
      {
        file: form.file,
        sheet: optional(form.sheet),
        range: optional(form.range),
        orientation: form.orientation,
        index: lineRef(form.index),
        labels: lineRef(form.labels),
        axis: optional(form.axis),
        description: optional(form.description),
      },
      mode,
      extra,
    );
    if (!result.committed) expectSave(false);
    else setUpload(null);
    setErrors(errorsOf(result));
    return result;
  };

  return (
    <div className="editor" style={{ maxWidth: "none" }}>
      <div className="editor-header">
        <h1>{name}</h1>
        <span className="pill">source</span>
        <div className="actions">
          <RenameDelete kind="source" tabKind="source" name={name} exists={Boolean(definition)} />
        </div>
      </div>
      <div className="form-grid" style={{ maxWidth: 760 }}>
        <label>File</label>
        <div className="row">
          <select className="input" style={{ width: 280 }} value={form.file} onChange={(e) => set("file", e.target.value)}>
            <option value="">choose a file…</option>
            {[...new Set([...state.files.map((f) => f.name), ...(upload ? [upload.path] : [])])].map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
          <label className="btn small">
            Upload…
            <input
              type="file"
              accept=".csv,.txt,.xlsx,.xlsm"
              style={{ display: "none" }}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const path = `inputs/${file.name}`;
                setUpload({ path, data: await readFile(file) });
                set("file", path);
              }}
            />
          </label>
        </div>
        <label>Sheet</label>
        <input type="text" placeholder="xlsx only" value={form.sheet} onChange={(e) => set("sheet", e.target.value)} />
        <label>Range</label>
        <input type="text" placeholder="whole sheet, or e.g. A1:H40" value={form.range} onChange={(e) => set("range", e.target.value)} />
        <label>Series run along</label>
        <div className="segmented">
          <button className={isRows ? "on" : ""} onClick={() => set("orientation", "rows")}>
            rows (statement layout)
          </button>
          <button className={!isRows ? "on" : ""} onClick={() => set("orientation", "columns")}>
            columns (table layout)
          </button>
        </div>
        <label>{isRows ? "Axis labels in row" : "Axis labels in column"}</label>
        <input type="text" placeholder={isRows ? "first row of the range" : "first column of the range"} value={form.index} onChange={(e) => set("index", e.target.value)} />
        <label>{isRows ? "Item names in column" : "Item names in row"}</label>
        <input type="text" placeholder={isRows ? "first column (letter)" : "first row (number)"} value={form.labels} onChange={(e) => set("labels", e.target.value)} />
        <label>Axis</label>
        <select value={form.axis} onChange={(e) => set("axis", e.target.value)}>
          <option value="">default axis</option>
          {Object.keys(state.model.axes ?? {}).map((a) => (
            <option key={a}>{a}</option>
          ))}
        </select>
        <label>Description</label>
        <input type="text" value={form.description} onChange={(e) => set("description", e.target.value)} />
      </div>
      <ActionBar dirty={dirty || Boolean(upload) || !definition} onSubmit={submit} onRevert={() => (revert(), setUpload(null))} errors={errors} />
      {definition && (
        <div className="card">
          <div className="card-title">
            What metrics can bind to
            {preview.data && <span className="muted">sheet {preview.data.sheet}</span>}
          </div>
          <div className="card-body" style={{ overflowX: "auto" }}>
            {preview.error ? (
              <div className="message error">{(preview.error as Error).message}</div>
            ) : preview.data ? (
              <table className="grid">
                <thead>
                  <tr>
                    <th>item</th>
                    {preview.data.periods.map((p) => (
                      <th key={p}>{p}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.data.rows.map((row) => (
                    <tr key={row.item}>
                      <td>{row.item}</td>
                      {row.values.map((v, i) => (
                        <td key={i}>{typeof v === "number" ? formatNumber(v) : v ?? "—"}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <span className="muted">Loading…</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
