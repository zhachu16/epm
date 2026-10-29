import { useState } from "react";
import type { AppState, BatchResult, Change, ImpactEntry, ValueDict } from "../api/types";
import { diffLines } from "../lib/diff";
import { formatNumber } from "../lib/format";
import { useStore } from "../store";

const show = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value, null, 2));

function ChangeLine({ change }: { change: Change }) {
  const [open, setOpen] = useState(change.change === "changed");
  const hasDiff = change.before !== undefined || change.after !== undefined;
  const lines = hasDiff ? diffLines(change.before === undefined ? "" : show(change.before), change.after === undefined ? "" : show(change.after)) : [];
  return (
    <div>
      <div className="change-line">
        <span className={`what ${change.change}`}>{change.change}</span>
        <span>
          {change.kind} {change.name}
          {change.renamed_from && <span className="muted"> (was {change.renamed_from})</span>}
        </span>
        {hasDiff && (
          <button className="icon-btn" onClick={() => setOpen(!open)}>
            {open ? "hide" : "diff"}
          </button>
        )}
      </div>
      {open && hasDiff && (
        <pre className="diff">
          {lines.map((l, i) => (
            <div key={i} className={l.kind === "del" ? "del" : l.kind === "ins" ? "ins" : ""}>
              {l.kind === "del" ? "- " : l.kind === "ins" ? "+ " : "  "}
              {l.text}
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}

function scalar(v: ValueDict | null): number | null {
  if (!v) return null;
  if (v.axis === null) return typeof v.value === "number" ? v.value : null;
  const entries = Object.values(v.values ?? {}).filter((x): x is number => typeof x === "number");
  return entries.length ? entries[entries.length - 1] : null;
}

function ImpactRow({ name, entry, unit }: { name: string; entry: ImpactEntry; unit?: string | null }) {
  const before = scalar(entry.before);
  const after = scalar(entry.after);
  const series = entry.after?.axis ?? entry.before?.axis;
  let delta: number | null = null;
  let changedPeriods: number | null = null;
  if (typeof entry.delta === "number") delta = entry.delta;
  else if (entry.delta && typeof entry.delta === "object") {
    const values = Object.values(entry.delta).filter((v): v is number => typeof v === "number");
    changedPeriods = values.filter((v) => Math.abs(v) > 1e-12).length;
    delta = before !== null && after !== null ? after - before : null;
  }
  const direction = delta === null || Math.abs(delta) < 1e-12 ? "" : delta > 0 ? "up" : "down";
  return (
    <tr>
      <td>{name}</td>
      <td>{entry.before === null ? "new" : formatNumber(before, unit)}</td>
      <td>{formatNumber(after, unit)}</td>
      <td className={`delta ${direction}`}>
        {delta === null ? "—" : `${delta > 0 ? "+" : ""}${formatNumber(delta, unit === "%" ? "%" : unit)}`}
        {series && changedPeriods !== null && <span className="muted"> · {changedPeriods} periods</span>}
      </td>
    </tr>
  );
}

/** What a batch changes: errors, definition diffs, effect on outputs and checks. */
export function BatchPreview({ state, result }: { state: AppState; result: BatchResult }) {
  const open = useStore((s) => s.open);
  const outputs = Object.entries(result.impact?.outputs ?? {});
  const checks = Object.entries(result.impact?.checks ?? {});
  return (
    <div className="batch" data-testid="batch-preview">
      <div className="label">{result.label}</div>
      {result.errors.length > 0 && (
        <div className="message error">
          {result.errors.map((e, i) => (
            <div key={i}>{e.message}</div>
          ))}
        </div>
      )}
      {result.changes.length > 0 && (
        <div>
          <div className="field-label muted" style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
            DEFINITIONS
          </div>
          {result.changes.map((c, i) => (
            <ChangeLine key={i} change={c} />
          ))}
        </div>
      )}
      {outputs.length > 0 && (
        <div>
          <div className="field-label muted" style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
            EFFECT ON OUTPUTS
          </div>
          <table className="grid" data-testid="impact">
            <thead>
              <tr>
                <th>output</th>
                <th>before</th>
                <th>after</th>
                <th>change</th>
              </tr>
            </thead>
            <tbody>
              {outputs.map(([name, entry]) => (
                <ImpactRow key={name} name={name} entry={entry} unit={state.metrics[name]?.unit} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {checks.length > 0 && (
        <div>
          {checks.map(([name, c]) => (
            <div key={name} className="change-line" onClick={() => open("check", name)} style={{ cursor: "pointer" }}>
              <span className={`what ${c.after === "passed" ? "added" : "removed"}`}>check</span>
              <span>
                {name}: {c.before ?? "—"} → {c.after ?? "—"}
              </span>
            </div>
          ))}
        </div>
      )}
      {result.new_errors.length > 0 && (
        <div className="message warning">
          New validation errors:
          {result.new_errors.map((d, i) => (
            <div key={i}>
              {d.object}: {d.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
