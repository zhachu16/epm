import { useEffect } from "react";
import { useStore } from "../store";

const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

/** How a shortcut reads on this platform: ⌘S on a Mac, Ctrl+S elsewhere. */
export const shortcut = (key: string) => (MAC ? `⌘${key}` : `Ctrl+${key}`);

/**
 * Ctrl+S (⌘S on a Mac) saves the open editor; Ctrl+Enter (⌘Enter) runs it, when it can run.
 * Listening in the capture phase lets these win over Monaco's own bindings, so they also
 * work while typing in a formula or code editor.
 */
function useEditorShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(MAC ? e.metaKey : e.ctrlKey) || e.altKey || e.shiftKey) return;
      const { editorActions: actions, readOnly } = useStore.getState();
      if (e.key.toLowerCase() === "s") {
        e.preventDefault(); // never the browser's "save page"
        e.stopPropagation();
        if (actions && actions.dirty && !actions.busy && !readOnly) actions.save();
      } else if (e.key === "Enter" && actions?.run && !actions.runDisabled && !actions.running) {
        e.preventDefault();
        e.stopPropagation();
        actions.run();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
}

/** Call the open editor's latest handler: they are replaced in place, without re-rendering. */
const latest = () => useStore.getState().editorActions;

/** The open editor's actions, at the top of the right column, with their errors. */
export function ActionPanel() {
  const actions = useStore((s) => s.editorActions);
  const readOnly = useStore((s) => s.readOnly);
  useEditorShortcuts();
  if (!actions) return null;
  const { dirty, busy, errors } = actions;
  return (
    <div className="action-panel" data-testid="action-panel">
      <div className="action-buttons">
        <button
          className="btn large primary"
          disabled={!dirty || busy || readOnly}
          onClick={() => latest()?.save()}
          title={readOnly ? "Read-only: make an editable copy to change it" : "Save a new version"}
          data-testid="save"
        >
          Save <kbd>{shortcut("S")}</kbd>
        </button>
        <button className="btn large" disabled={!dirty || busy} onClick={() => latest()?.preview()} title="Show the change and its effect on key outputs, without saving" data-testid="preview">
          Preview
        </button>
        {actions.revert && (
          <button className="btn large" disabled={!dirty || busy} onClick={() => latest()?.revert?.()} title="Discard unsaved edits" data-testid="revert">
            Revert
          </button>
        )}
        {actions.run && (
          <button className="btn large run" disabled={actions.runDisabled || actions.running} onClick={() => latest()?.run?.()} data-testid="run">
            {actions.running ? "Running…" : "Run"} <kbd>{shortcut("↵")}</kbd>
          </button>
        )}
      </div>
      <div className={`action-status ${dirty ? "dirty" : ""}`} data-testid="action-status">
        {busy ? "Working…" : dirty ? "Unsaved changes" : "No unsaved changes"}
      </div>
      {actions.extras && actions.extras.length > 0 && (
        <div className="action-buttons">
          {actions.extras.map((e) =>
            e.href ? (
              <a key={e.id} className="btn small" href={e.href} target="_blank" rel="noreferrer" title={e.title} data-testid={e.id}>
                {e.label}
              </a>
            ) : (
              <button
                key={e.id}
                className="btn small"
                disabled={e.disabled}
                onClick={() => latest()?.extras?.find((x) => x.id === e.id)?.onClick?.()}
                title={e.title}
                data-testid={e.id}
              >
                {e.label}
              </button>
            ),
          )}
        </div>
      )}
      {errors.length > 0 && (
        <div className="message error" data-testid="editor-errors">
          {errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}
    </div>
  );
}
