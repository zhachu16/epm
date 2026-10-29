import { useEffect, useRef, useState } from "react";
import type { Mode } from "../api/client";
import { useApply } from "../api/hooks";
import type { BatchResult, Op, OpKind } from "../api/types";
import { type ExtraAction, type TabKind, useStore } from "../store";

/**
 * Hands the open editor's Preview / Save / Revert (and Run, where there is one) to the
 * action panel above the inspector, which shows them, their errors and the keyboard
 * shortcuts. Renders nothing in the editor itself; ``extras`` are more buttons for the panel.
 */
export function ActionBar({
  dirty,
  onSubmit,
  onRevert,
  errors,
  onRun,
  running,
  runDisabled,
  extras,
}: {
  dirty: boolean;
  onSubmit: (mode: Mode) => Promise<unknown>;
  onRevert?: () => void;
  errors: string[];
  onRun?: () => void;
  running?: boolean;
  runDisabled?: boolean;
  extras?: ExtraAction[];
}) {
  const [busy, setBusy] = useState(false);
  const setEditorActions = useStore((s) => s.setEditorActions);
  const run = async (mode: Mode) => {
    if (busy) return;
    setBusy(true);
    try {
      await onSubmit(mode);
    } finally {
      setBusy(false);
    }
  };
  // every render: the handlers close over the editor's latest draft
  useEffect(() => {
    setEditorActions({
      dirty,
      busy,
      errors,
      preview: () => void run("dry_run"),
      save: () => void run("commit"),
      revert: onRevert,
      run: onRun,
      running,
      runDisabled,
      extras,
    });
  });
  useEffect(() => () => setEditorActions(null), [setEditorActions]);
  return null;
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
