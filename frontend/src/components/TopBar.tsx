import { api, ApiError } from "../api/client";
import { useRefresh } from "../api/hooks";
import type { AppState } from "../api/types";
import { plural } from "../lib/format";
import { useStore } from "../store";

export function TopBar({ state }: { state: AppState }) {
  const refresh = useRefresh();
  const { setBottom, log, connected } = useStore();
  const checks = Object.values(state.checks);
  const failed = checks.filter((c) => c.state !== "passed");
  const errors = state.diagnostics.filter((d) => d.severity === "error").length;
  const warnings = state.diagnostics.filter((d) => d.severity === "warning").length;

  const step = async (action: "undo" | "redo") => {
    try {
      const entry = await api[action]();
      log("info", `${action === "undo" ? "undid" : "redid"}: ${entry.label}`);
    } catch (e) {
      log("error", e instanceof ApiError ? e.message : String(e));
    }
    await refresh();
  };

  return (
    <header className="topbar">
      <span className="title">{state.name}</span>
      <span className="path" title={state.path}>
        {state.path}
      </span>
      <span className="spacer" />
      <button
        className={`pill ${failed.length ? "error" : "ok"}`}
        onClick={() => setBottom("problems")}
        title="Checks"
        data-testid="checks-pill"
      >
        <span className={`dot ${failed.length ? "failed" : "ok"}`} />
        {failed.length ? `${plural(failed.length, "check")} failing` : `${plural(checks.length, "check")} passing`}
      </button>
      <button
        className={`pill ${errors ? "error" : warnings ? "warn" : ""}`}
        onClick={() => setBottom("problems")}
        title="Problems"
      >
        {errors} errors · {warnings} warnings
      </button>
      <button className="btn small" disabled={!state.history.length} onClick={() => step("undo")} title={state.history.at(-1)?.label} data-testid="undo">
        ↶ Undo
      </button>
      <button className="btn small" disabled={!state.redo.length} onClick={() => step("redo")} title={state.redo.at(-1)?.label} data-testid="redo">
        ↷ Redo
      </button>
      <span className="pill" title={connected ? "Live: changes from the CLI and agents appear here" : "Reconnecting…"}>
        <span className={`dot ${connected ? "ok" : "blocked"}`} />
        {connected ? "live" : "offline"}
      </span>
    </header>
  );
}
