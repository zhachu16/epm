import type { AppState, ValueDict } from "../api/types";
import { formatNumber } from "../lib/format";

export interface ValueRow {
  key: string;
  label: string;
  metric?: string;
  unit?: string | null;
  value?: ValueDict | null;
  style?: "normal" | "subtotal" | "total" | "header";
}

/** Rows of metrics against the periods of an axis, historical periods shown muted. */
export function ValuesTable({
  state,
  rows,
  onOpen,
}: {
  state: AppState;
  rows: ValueRow[];
  onOpen?: (metric: string) => void;
}) {
  const axisName = rows.find((r) => r.value?.axis)?.value?.axis ?? null;
  const axis = axisName ? state.model.axes?.[axisName] : undefined;
  const periods = axis ? axis.values.map(String) : [];
  const historical = new Set((axis?.historical ?? []).map(String));
  const scalarOnly = periods.length === 0;

  return (
    <table className="grid">
      <thead>
        <tr>
          <th>{axisName ?? ""}</th>
          {scalarOnly ? <th>value</th> : periods.map((p) => <th key={p}>{historical.has(p) ? `${p}A` : `${p}E`}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          if (row.style === "header") {
            return (
              <tr key={row.key} className="header">
                <td colSpan={periods.length + 1}>{row.label}</td>
              </tr>
            );
          }
          const value = row.value;
          const cells = scalarOnly
            ? [value?.axis === null ? value.value : null]
            : periods.map((p) => (value?.axis ? value.values?.[p] : p === periods[periods.length - 1] ? value?.value : null));
          return (
            <tr key={row.key} className={`${row.style ?? ""} ${row.metric && onOpen ? "clickable" : ""}`}>
              <td onClick={() => row.metric && onOpen?.(row.metric)} title={row.metric}>
                {row.label}
              </td>
              {cells.map((v, i) => (
                <td key={i} className={`${!scalarOnly && historical.has(periods[i]) ? "hist" : ""} ${v === null || v === undefined ? "missing" : ""}`}>
                  {v === null || v === undefined ? (value?.axis === null && !scalarOnly ? "" : "—") : formatNumber(v, row.unit)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
