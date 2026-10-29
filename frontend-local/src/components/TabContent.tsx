import type { AppState } from "../api/types";
import { AnalysisEditor } from "../editors/AnalysisEditor";
import { DefinitionEditor } from "../editors/DefinitionEditor";
import { FunctionEditor } from "../editors/FunctionEditor";
import { MetricEditor } from "../editors/MetricEditor";
import { SourceEditor } from "../editors/SourceEditor";
import { TableEditor } from "../editors/TableEditor";
import { TextFileEditor } from "../editors/TextFileEditor";
import { type Tab, type TabKind, useStore } from "../store";

/** Kinds with a form that can also be edited as their JSON definition. */
const JSON_KINDS = new Set<TabKind>(["metric", "table", "analysis", "source"]);

/** The editor for an open tab. Objects that do not exist yet open as new, unsaved drafts. */
export function TabContent({ state, tab }: { state: AppState; tab: Tab }) {
  const json = useStore((s) => Boolean(s.jsonView[tab.id]));
  if (!JSON_KINDS.has(tab.kind)) return <Editor state={state} tab={tab} />;
  return (
    <>
      <ViewToggle tab={tab} json={json} />
      {json ? <DefinitionEditor state={state} kind={tab.kind} name={tab.name} /> : <Editor state={state} tab={tab} />}
    </>
  );
}

function Editor({ state, tab }: { state: AppState; tab: Tab }) {
  switch (tab.kind) {
    case "metric":
      return <MetricEditor state={state} name={tab.name} />;
    case "table":
      return <TableEditor state={state} name={tab.name} />;
    case "function":
      return <FunctionEditor state={state} name={tab.name} />;
    case "analysis":
      return <AnalysisEditor state={state} name={tab.name} />;
    case "source":
      return <SourceEditor state={state} name={tab.name} />;
    case "model.json":
    case "functions.py":
      return <TextFileEditor state={state} file={tab.kind} />;
    default:
      return <DefinitionEditor state={state} kind={tab.kind} name={tab.name} />;
  }
}

/** Switch between the form and the definition's JSON; either saves through the same ops. */
function ViewToggle({ tab, json }: { tab: Tab; json: boolean }) {
  const setJsonView = useStore((s) => s.setJsonView);
  const choose = (next: boolean) => {
    if (next === json) return;
    const actions = useStore.getState().editorActions;
    if (actions?.dirty && !window.confirm("Switching views discards your unsaved edits. Switch anyway?")) return;
    setJsonView(tab.id, next);
  };
  return (
    <div className="view-bar">
      <div className="segmented" role="group" aria-label="Editor view">
        <button className={json ? "" : "on"} onClick={() => choose(false)} data-testid="view-form">
          Form
        </button>
        <button className={json ? "on" : ""} onClick={() => choose(true)} title="Edit the definition as JSON" data-testid="view-json">
          JSON
        </button>
      </div>
    </div>
  );
}
