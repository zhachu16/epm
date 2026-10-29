import type { AppState } from "../api/types";
import { type Tab, useStore } from "../store";
import { ValuesTable, type ValueRow } from "./ValuesTable";

/** Read-only views of each object kind; the editors replace these. */
export function TabContent({ state, tab }: { state: AppState; tab: Tab }) {
  const open = useStore((s) => s.open);
  if (tab.kind === "metric") {
    const info = state.metrics[tab.name];
    if (!info) return <div className="empty">Metric '{tab.name}' no longer exists.</div>;
    return (
      <div className="editor">
        <div className="editor-header">
          <h1>{tab.name}</h1>
        </div>
        {info.formula && <div className="codeblock">{info.formula}</div>}
        <ValuesTable state={state} rows={[{ key: tab.name, label: tab.name, unit: info.unit, value: info.value }]} />
      </div>
    );
  }
  if (tab.kind === "table") {
    const table = state.model.tables?.[tab.name];
    if (!table) return <div className="empty">Table '{tab.name}' no longer exists.</div>;
    const rows: ValueRow[] = table.rows.map((row, i) =>
      row.header
        ? { key: `h${i}`, label: row.header, style: "header" }
        : {
            key: `${i}-${row.metric}`,
            label: row.label ?? row.metric ?? "",
            metric: row.metric,
            unit: state.metrics[row.metric!]?.unit,
            value: state.metrics[row.metric!]?.value,
            style: row.style,
          },
    );
    return (
      <div className="editor" style={{ maxWidth: "none" }}>
        <div className="editor-header">
          <h1>{tab.name}</h1>
        </div>
        <ValuesTable state={state} rows={rows} onOpen={(m) => open("metric", m)} />
      </div>
    );
  }
  const definition =
    tab.kind === "model.json"
      ? state.model
      : tab.kind === "functions.py"
        ? state.functions_source
        : tab.kind === "settings"
          ? state.model.settings
          : tab.kind === "function"
            ? state.functions.find((f) => f.name === tab.name)?.source
            : (state.model as Record<string, Record<string, unknown> | undefined>)[
                { analysis: "analyses", source: "sources", check: "checks", circularity: "circularities", axis: "axes" }[
                  tab.kind as string
                ] ?? ""
              ]?.[tab.name];
  return (
    <div className="editor">
      <div className="editor-header">
        <h1>{tab.name}</h1>
      </div>
      <div className="codeblock">{typeof definition === "string" ? definition : JSON.stringify(definition, null, 2)}</div>
    </div>
  );
}
