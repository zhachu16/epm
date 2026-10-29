import type { AppState } from "../api/types";
import { AnalysisEditor } from "../editors/AnalysisEditor";
import { DefinitionEditor } from "../editors/DefinitionEditor";
import { FunctionEditor } from "../editors/FunctionEditor";
import { MetricEditor } from "../editors/MetricEditor";
import { SourceEditor } from "../editors/SourceEditor";
import { TableEditor } from "../editors/TableEditor";
import { TextFileEditor } from "../editors/TextFileEditor";
import type { Tab } from "../store";

/** The editor for an open tab. Objects that do not exist yet open as new, unsaved drafts. */
export function TabContent({ state, tab }: { state: AppState; tab: Tab }) {
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
