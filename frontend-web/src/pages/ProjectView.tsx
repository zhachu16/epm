import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { bindProject, errorText, projects, setLoadedVersion } from "../api/client";
import { useAppState, useRefresh } from "../api/hooks";
import { BottomPanel } from "../components/BottomPanel";
import { Center } from "../components/Center";
import { Explorer } from "../components/Explorer";
import { Inspector } from "../components/Inspector";
import { TopBar } from "../components/TopBar";
import { setFormulaContext } from "../lib/monaco";
import { onLinkClick } from "../lib/router";
import { hasUnsavedEdits, useStore } from "../store";

/** How often to look for versions saved elsewhere (an agent over MCP, another tab). */
const POLL_MS = 5000;

export function ProjectView({ id, version }: { id: string; version?: number }) {
  bindProject(id);
  const { data: state, error, isLoading } = useAppState(id, version);
  const reset = useStore((s) => s.reset);

  useEffect(() => {
    reset();
  }, [id, reset]);

  useEffect(() => {
    if (!state) return;
    setFormulaContext(state);
    const current = state.version === state.project.current_version;
    useStore.getState().setReadOnly(state.read_only);
    if (current && version === undefined) setLoadedVersion(state.version);
    document.title = `${state.project.name} · epm`;
  }, [state, version]);

  useWarnBeforeLeaving();

  if (isLoading) return <div className="empty">Loading project…</div>;
  if (error || !state) {
    return (
      <div className="empty">
        <p>Could not open this project.</p>
        <p className="muted">{errorText(error)}</p>
        <p>
          <a href="/" onClick={onLinkClick("/")}>
            All projects
          </a>
        </p>
      </div>
    );
  }

  return (
    <div className="app">
      <TopBar state={state} />
      {version === undefined && <OutsideChanges id={id} version={state.version} />}
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

/**
 * Notice versions saved elsewhere. With nothing unsaved here, load them straight away;
 * otherwise offer to, so open edits are not thrown away.
 */
function OutsideChanges({ id, version }: { id: string; version: number }) {
  const refresh = useRefresh();
  const unsaved = useStore(hasUnsavedEdits);
  const saving = useStore((s) => s.save.state === "saving");
  const { data: project } = useQuery({
    queryKey: ["project", id],
    queryFn: () => projects.get(id),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  });
  const newer = project !== undefined && project.current_version > version && !saving;

  useEffect(() => {
    if (newer && !unsaved) {
      useStore.getState().log("info", `loaded version ${project.current_version}, saved elsewhere`);
      void refresh();
    }
  }, [newer, unsaved, project, refresh]);

  if (!newer || !unsaved) return null;
  return (
    <div className="banner warn" data-testid="outside-changes">
      Version {project.current_version} was saved elsewhere (an agent or another window). Your unsaved edits are based on
      version {version}.
      <button className="btn small" onClick={() => refresh()}>
        Load version {project.current_version}
      </button>
    </div>
  );
}

function useWarnBeforeLeaving() {
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (hasUnsavedEdits(useStore.getState())) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
}
