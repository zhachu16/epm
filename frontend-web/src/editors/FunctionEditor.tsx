import { useMemo, useState } from "react";
import type { AppState } from "../api/types";
import { useStore } from "../store";
import { CodeEditor } from "./CodeEditor";
import { ActionBar, errorsOf, RenameDelete, useDraft, useUpsert } from "./common";

const template = (name: string) => `@function\ndef ${name}(x):\n    """Describe what it returns."""\n    return x\n`;

export function FunctionEditor({ state, name }: { state: AppState; name: string }) {
  const fn = state.functions.find((f) => f.name === name);
  const source = useMemo(() => (fn ? `${fn.source}\n` : template(name)), [fn, name]);
  const { draft, setDraft, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const upsert = useUpsert("function", name);
  const open = useStore((s) => s.open);
  const callers = Object.entries(state.metrics)
    .filter(([, m]) => m.formula?.includes(`${name}(`))
    .map(([n]) => n);

  const submit = async (mode: "commit" | "dry_run") => {
    expectSave(mode === "commit");
    const result = await upsert({ code: draft }, mode);
    if (!result.committed) expectSave(false);
    setErrors(errorsOf(result));
    return result;
  };

  return (
    <div className="editor" style={{ maxWidth: "none", height: "100%" }}>
      <div className="editor-header">
        <h1>{name}</h1>
        {fn ? <span className="pill">{fn.signature} → {fn.returns}</span> : <span className="pill">new</span>}
        <div className="actions">
          <button className="btn small" onClick={() => open("functions.py", "functions.py")}>
            Open functions.py
          </button>
          <RenameDelete kind="function" tabKind="function" name={name} exists={Boolean(fn)} />
        </div>
      </div>
      <CodeEditor value={draft} language="python" onChange={setDraft} height={340} testId="function-code" />
      <div className="muted">
        Runs in an isolated worker with time and memory limits. Imports are limited to numpy, pandas and the standard maths
        modules; dependencies arrive only as arguments.
        {callers.length > 0 && ` Called by ${callers.join(", ")}.`}
      </div>
      <ActionBar dirty={dirty || !fn} onSubmit={submit} onRevert={revert} errors={errors} />
    </div>
  );
}
