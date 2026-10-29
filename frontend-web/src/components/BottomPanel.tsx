import type { AppState, Diagnostic } from "../api/types";
import { type BottomTab, type TabKind, useStore } from "../store";
import { ChangesPanel } from "./ChangesPanel";
import { FilesPanel, OutputsPanel, VersionsPanel } from "./ProjectPanels";

const KINDS = new Set<TabKind>(["metric", "table", "function", "analysis", "source", "check", "circularity", "axis"]);

export function BottomPanel({ state }: { state: AppState }) {
  const { bottom, setBottom } = useStore();
  const problems = state.diagnostics.filter((d) => d.severity !== "info");
  const failedChecks = Object.entries(state.checks).filter(([, c]) => c.state !== "passed");
  const preview = useStore((s) => s.preview);
  const tabs: { id: BottomTab; label: string; count?: number; hot?: boolean; bad?: boolean }[] = [
    { id: "changes", label: "Changes", count: preview ? 1 : 0, hot: Boolean(preview) },
    { id: "problems", label: "Problems", count: problems.length + failedChecks.length, bad: problems.some((p) => p.severity === "error") },
    { id: "versions", label: "Versions" },
    { id: "files", label: "Files", count: state.files.length },
    { id: "outputs", label: "Outputs" },
    { id: "logs", label: "Logs" },
  ];
  return (
    <div className="panel">
      <div className="bottom-tabs">
        {tabs.map((t) => (
          <button key={t.id} className={bottom === t.id ? "on" : ""} onClick={() => setBottom(t.id)} data-testid={`bottom-${t.id}`}>
            {t.label}
            {t.count !== undefined && t.count > 0 && <span className={`badge ${t.hot ? "hot" : t.bad ? "bad" : ""}`}>{t.count}</span>}
          </button>
        ))}
      </div>
      <div className="panel-body">
        {bottom === "problems" && <Problems state={state} problems={problems} />}
        {bottom === "logs" && <Logs />}
        {bottom === "changes" && <ChangesPanel state={state} />}
        {bottom === "versions" && <VersionsPanel state={state} />}
        {bottom === "files" && <FilesPanel state={state} />}
        {bottom === "outputs" && <OutputsPanel state={state} />}
      </div>
    </div>
  );
}

export function objectOf(d: Diagnostic): { kind: TabKind; name: string } | null {
  if (!d.object) return null;
  const [kind, ...rest] = d.object.split(":");
  const name = rest.join(":");
  return KINDS.has(kind as TabKind) && name ? { kind: kind as TabKind, name } : null;
}

function Problems({ state, problems }: { state: AppState; problems: Diagnostic[] }) {
  const open = useStore((s) => s.open);
  const failed = Object.entries(state.checks).filter(([, c]) => c.state !== "passed");
  if (!problems.length && !failed.length) return <div className="empty">No problems: the model validates and every check passes.</div>;
  return (
    <div data-testid="problems">
      {failed.map(([name, c]) => (
        <div key={`check-${name}`} className="list-row clickable" onClick={() => open("check", name)}>
          <span className={`dot ${c.severity === "warning" ? "warning" : "error"}`} />
          <span className="obj">check:{name}</span>
          <span>{c.message ?? c.state}</span>
        </div>
      ))}
      {problems.map((d, i) => {
        const target = objectOf(d);
        return (
          <div key={i} className={`list-row ${target ? "clickable" : ""}`} onClick={() => target && open(target.kind, target.name)}>
            <span className={`dot ${d.severity === "error" ? "error" : "warning"}`} />
            <span className="obj">{d.object ?? "model"}</span>
            <span>{d.message}</span>
            <span className="muted">{d.code}</span>
          </div>
        );
      })}
    </div>
  );
}

function Logs() {
  const logs = useStore((s) => s.logs);
  if (!logs.length) return <div className="empty">Saves, restores, reloads and runs are logged here.</div>;
  return (
    <div>
      {[...logs].reverse().map((entry, i) => (
        <div key={i} className="list-row">
          <span className="when">{new Date(entry.at * 1000).toLocaleTimeString()}</span>
          <span className={`dot ${entry.level === "error" ? "error" : entry.level === "warning" ? "warning" : "ok"}`} />
          <span>{entry.message}</span>
        </div>
      ))}
    </div>
  );
}
