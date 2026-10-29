import { useMemo, useState } from "react";
import type { Mode } from "../api/client";
import { useApply } from "../api/hooks";
import type { AppState, OpKind } from "../api/types";
import type { TabKind } from "../store";
import { CodeEditor } from "./CodeEditor";
import { ActionBar, errorsOf, RenameDelete, useDraft, withCleared } from "./common";

const COLLECTION: Record<string, keyof AppState["model"]> = {
  metric: "metrics",
  table: "tables",
  check: "checks",
  circularity: "circularities",
  axis: "axes",
  source: "sources",
  analysis: "analyses",
};

const TEMPLATES: Record<string, unknown> = {
  metric: { value: 0 },
  table: { rows: [] },
  check: { expr: "total_assets - total_liabilities_and_equity", tolerance: 1e-6 },
  circularity: { metrics: [] },
  axis: { values: [2024, 2025, 2026], historical: [2024] },
};

/** JSON editing of a definition: for kinds without a form, and the JSON view of the others. */
export function DefinitionEditor({ state, kind, name }: { state: AppState; kind: TabKind; name: string }) {
  const isSettings = kind === "settings";
  const current = isSettings
    ? state.model.settings ?? {}
    : (state.model[COLLECTION[kind]] as Record<string, unknown> | undefined)?.[name];
  const source = useMemo(() => JSON.stringify(current ?? TEMPLATES[kind] ?? {}, null, 2) + "\n", [current, kind]);
  const { draft, setDraft, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const apply = useApply();
  const check = kind === "check" ? state.checks[name] : undefined;
  const solver = kind === "circularity" ? state.solver[name] : undefined;

  const submit = async (mode: Mode) => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(draft);
    } catch (e) {
      setErrors([`not valid JSON: ${(e as Error).message}`]);
      return;
    }
    expectSave(mode === "commit");
    const fields = withCleared(parsed, current as Record<string, unknown> | undefined);
    const result = await apply(
      isSettings ? [{ op: "update", kind: "settings", set: fields }] : [{ op: "upsert", kind: kind as OpKind, name, set: fields }],
      mode,
    );
    if (!result.committed) expectSave(false);
    setErrors(errorsOf(result));
    return result;
  };

  return (
    <div className="editor">
      <div className="editor-header">
        <h1>{name}</h1>
        <span className="pill">{kind}</span>
        {!current && !isSettings && <span className="pill">new</span>}
        <div className="actions">{!isSettings && <RenameDelete kind={kind as OpKind} tabKind={kind} name={name} exists={Boolean(current)} />}</div>
      </div>
      {check && (
        <div className={`message ${check.state === "passed" ? "ok" : check.severity === "warning" ? "warning" : "error"}`}>
          {check.state}
          {check.message ? `: ${check.message}` : ""}
          {check.failing.length > 0 &&
            ` (${check.failing
              .slice(0, 6)
              .map((f) => `${f.period ?? ""} ${f.value === null ? "missing" : f.value.toPrecision(4)}`)
              .join(", ")})`}
        </div>
      )}
      {solver && (
        <div className={`message ${solver.converged ? "ok" : "error"}`}>
          {solver.converged ? `converged in up to ${solver.iterations} iterations` : solver.message}
          {solver.periods && ` · ${Object.entries(solver.periods).map(([p, n]) => `${p}: ${n}`).join(", ")}`}
        </div>
      )}
      <CodeEditor value={draft} language="json" onChange={setDraft} height={320} testId="definition-json" />
      <ActionBar dirty={dirty || (!current && !isSettings)} onSubmit={submit} onRevert={revert} errors={errors} />
    </div>
  );
}
