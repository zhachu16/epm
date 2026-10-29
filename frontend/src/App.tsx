import { useEffect } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { useServerEvents } from "./api/events";
import { useAppState } from "./api/hooks";
import { BottomPanel } from "./components/BottomPanel";
import { Center } from "./components/Center";
import { Explorer } from "./components/Explorer";
import { Inspector } from "./components/Inspector";
import { TopBar } from "./components/TopBar";
import { setFormulaContext } from "./lib/monaco";

export default function App() {
  useServerEvents();
  const { data: state, error, isLoading } = useAppState();
  useEffect(() => {
    if (state) setFormulaContext(state);
  }, [state]);

  if (isLoading) return <div className="empty">Loading model…</div>;
  if (error || !state) {
    return (
      <div className="empty">
        <p>Could not reach the epm session.</p>
        <p className="muted">{error instanceof Error ? error.message : "Is `fm serve` still running?"}</p>
      </div>
    );
  }

  return (
    <div className="app">
      <TopBar state={state} />
      <PanelGroup direction="horizontal" autoSaveId="epm-main" style={{ flex: 1, minHeight: 0 }}>
        <Panel defaultSize={18} minSize={12} order={1}>
          <Explorer state={state} />
        </Panel>
        <PanelResizeHandle className="resize-handle" />
        <Panel defaultSize={58} minSize={30} order={2}>
          <PanelGroup direction="vertical" autoSaveId="epm-center">
            <Panel defaultSize={66} minSize={20} order={1}>
              <Center state={state} />
            </Panel>
            <PanelResizeHandle className="resize-handle" />
            <Panel defaultSize={34} minSize={8} order={2}>
              <BottomPanel state={state} />
            </Panel>
          </PanelGroup>
        </Panel>
        <PanelResizeHandle className="resize-handle" />
        <Panel defaultSize={24} minSize={14} order={3}>
          <Inspector state={state} />
        </Panel>
      </PanelGroup>
    </div>
  );
}
