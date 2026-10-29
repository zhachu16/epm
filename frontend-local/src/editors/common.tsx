import { useEffect, useRef, useState } from "react";
import type { Mode } from "../api/client";
import { useApply } from "../api/hooks";
import type { BatchResult, Op, OpKind } from "../api/types";
import { type TabKind, useStore } from "../store";

/** Preview / Save / Revert, plus the errors from the last attempt. */
export function ActionBar({
  dirty,
  onSubmit,
  onRevert,
  errors,
  children,
}: {
  dirty: boolean;
  onSubmit: (mode: Mode) => Promise<unknown>;
  onRevert?: () => void;
  errors: string[];
  children?: React.ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const run = async (mode: Mode) => {
    setBusy(true);
    try {
      await onSubmit(mode);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="row">
        <button className="btn" disabled={!dirty || busy} onClick={() => run("dry_run")} data-testid="preview" title="Show the change and its effect without saving">
          Preview
        </button>
        <button className="btn primary" disabled={!dirty || busy} onClick={() => run("commit")} data-testid="save">
          Save
        </button>
        {onRevert && (
          <button className="btn" disabled={!dirty || busy} onClick={onRevert}>
            Revert
          </button>
        )}
        {children}
      </div>
      {errors.length > 0 && (
        <div className="message error" data-testid="editor-errors">
          {errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}
    </>
  );
}

export function errorsOf(result: BatchResult): string[] {
  return result.ok ? [] : result.errors.map((e) => (e.op && e.op !== "validation" && e.op !== "batch" ? `${e.message}` : e.message));
}

/** Rename and delete buttons for any named object. */
export function RenameDelete({ kind, tabKind, name, exists }: { kind: OpKind; tabKind: TabKind; name: string; exists: boolean }) {
  const apply = useApply();
  const { renameTab, close, log } = useStore();
  if (!exists) return null;
  const rename = async () => {
    const to = window.prompt(`Rename ${kind} '${name}' to:`, name)?.trim();
    if (!to || to === name) return;
    const result = await apply([{ op: "rename", kind, name, to }]);
    if (result.ok) renameTab(tabKind, name, to);
  };
  const remove = async () => {
    if (!window.confirm(`Delete ${kind} '${name}'?`)) return;
    const result = await apply([{ op: "delete", kind, name }]);
    if (result.ok) close(`${tabKind}:${name}`);
    else log("error", result.errors[0]?.message ?? "delete failed");
  };
  return (
    <>
      <button className="btn small" onClick={rename} data-testid="rename">
        Rename
      </button>
      <button className="btn small danger" onClick={remove} data-testid="delete">
        Delete
      </button>
    </>
  );
}

/** Upsert an object, returning the result so editors can show errors. */
export function useUpsert(kind: OpKind, name: string) {
  const apply = useApply();
  return (fields: Record<string, unknown>, mode: Mode, extra: Op[] = []) =>
    apply([...extra, { op: "upsert", kind, name, set: fields }], mode);
}

/** Fields of ``before`` missing from ``after`` become null, so an update clears them. */
export function withCleared(after: Record<string, unknown>, before: Record<string, unknown> | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = { ...after };
  for (const key of Object.keys(before ?? {})) if (!(key in out)) out[key] = null;
  return out;
}

/**
 * Local edits of a saved definition. When the saved version changes (undo, the CLI, an
 * agent), the draft follows it, unless it has unsaved edits of its own. Call
 * ``expectSave`` just before committing so the draft adopts what the server saves.
 */
export function useDraft<T>(source: T) {
  const saved = JSON.stringify(source);
  const [draft, setDraft] = useState<T>(source);
  const previous = useRef(saved);
  const latest = useRef(source);
  const adopt = useRef(false);
  latest.current = source;
  useEffect(() => {
    if (saved === previous.current) return;
    const before = previous.current;
    const take = adopt.current;
    adopt.current = false;
    setDraft((d) => (take || JSON.stringify(d) === before ? latest.current : d));
    previous.current = saved;
  }, [saved]);
  return {
    draft,
    setDraft,
    dirty: JSON.stringify(draft) !== saved,
    revert: () => setDraft(latest.current),
    expectSave: (expect: boolean) => {
      adopt.current = expect;
    },
  };
}
