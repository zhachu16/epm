import { useMemo, useState } from "react";
import type { AppState, TableDef, TableRow } from "../api/types";
import { ValuesTable, type ValueRow } from "../components/ValuesTable";
import { useStore } from "../store";
import { ActionBar, errorsOf, RenameDelete, useDraft, useUpsert } from "./common";

const STYLES: TableRow["style"][] = [undefined, "subtotal", "total"];

export function TableEditor({ state, name }: { state: AppState; name: string }) {
  const definition = state.model.tables?.[name];
  const source = useMemo<TableDef>(() => definition ?? { rows: [] }, [definition]);
  const { draft, setDraft, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const [adding, setAdding] = useState("");
  const upsert = useUpsert("table", name);
  const open = useStore((s) => s.open);

  const rows = draft.rows;
  const setRows = (next: TableRow[]) => setDraft({ ...draft, rows: next });
  const move = (i: number, by: number) => {
    const next = [...rows];
    const [row] = next.splice(i, 1);
    next.splice(Math.max(0, Math.min(next.length, i + by)), 0, row);
    setRows(next);
  };
  const update = (i: number, patch: Partial<TableRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const preview: ValueRow[] = rows.map((row, i) =>
    row.header
      ? { key: `h${i}`, label: row.header, style: "header" }
      : {
          key: `${i}-${row.metric}`,
          label: row.label || row.metric || "",
          metric: row.metric,
          unit: state.metrics[row.metric ?? ""]?.unit,
          value: state.metrics[row.metric ?? ""]?.value,
          style: row.style,
        },
  );

  const submit = async (mode: "commit" | "dry_run" | "propose") => {
    expectSave(mode === "commit");
    const cleanRows = rows.map((r) => Object.fromEntries(Object.entries(r).filter(([, v]) => v !== undefined && v !== "")));
    const result = await upsert({ rows: cleanRows, axis: draft.axis ?? null, folder: draft.folder ?? null }, mode);
    if (!result.committed) expectSave(false);
    setErrors(errorsOf(result));
    return result;
  };

  return (
    <div className="editor" style={{ maxWidth: "none" }} data-testid="table-editor">
      <div className="editor-header">
        <h1>{name}</h1>
        {!definition && <span className="pill">new</span>}
        <div className="actions">
          <RenameDelete kind="table" tabKind="table" name={name} exists={Boolean(definition)} />
        </div>
      </div>
      <div className="card">
        <div className="card-body" style={{ overflowX: "auto" }}>
          <ValuesTable state={state} rows={preview} onOpen={(m) => open("metric", m)} />
        </div>
      </div>
      <div className="card">
        <div className="card-title">Rows</div>
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {rows.map((row, i) => (
            <div key={i} className="row">
              <button className="icon-btn" onClick={() => move(i, -1)} disabled={i === 0} title="Move up">
                ↑
              </button>
              <button className="icon-btn" onClick={() => move(i, 1)} disabled={i === rows.length - 1} title="Move down">
                ↓
              </button>
              {row.header !== undefined ? (
                <input className="input" style={{ width: 260 }} value={row.header} onChange={(e) => update(i, { header: e.target.value })} placeholder="Header" />
              ) : (
                <>
                  <span className="mono" style={{ width: 220 }}>
                    {row.metric}
                    {!state.metrics[row.metric ?? ""] && <span className="dot error" style={{ marginLeft: 6 }} title="unknown metric" />}
                  </span>
                  <input className="input" style={{ width: 200 }} placeholder="label" value={row.label ?? ""} onChange={(e) => update(i, { label: e.target.value || undefined })} />
                  <select className="input" style={{ width: 110 }} value={row.style ?? ""} onChange={(e) => update(i, { style: (e.target.value || undefined) as TableRow["style"] })}>
                    {STYLES.map((s) => (
                      <option key={s ?? "normal"} value={s ?? ""}>
                        {s ?? "normal"}
                      </option>
                    ))}
                  </select>
                </>
              )}
              <button className="icon-btn" onClick={() => setRows(rows.filter((_, j) => j !== i))} title="Remove row">
                ×
              </button>
            </div>
          ))}
          <div className="row">
            <select className="input" style={{ width: 260 }} value={adding} onChange={(e) => setAdding(e.target.value)} data-testid="table-add-metric">
              <option value="">add a metric…</option>
              {Object.keys(state.metrics).map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
            <button className="btn small" disabled={!adding} onClick={() => (setRows([...rows, { metric: adding }]), setAdding(""))}>
              Add row
            </button>
            <button className="btn small" onClick={() => setRows([...rows, { header: "Section" }])}>
              Add header
            </button>
          </div>
        </div>
      </div>
      <ActionBar dirty={dirty || !definition} onSubmit={submit} onRevert={revert} errors={errors} />
    </div>
  );
}
