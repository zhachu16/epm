import type { AppState } from "../api/types";
import { type Tab, useStore } from "../store";
import { GraphView } from "./GraphView";
import { TabContent } from "./TabContent";

const KIND_LABEL: Record<string, string> = {
  metric: "metric",
  table: "table",
  function: "fn",
  analysis: "analysis",
  source: "source",
  check: "check",
  circularity: "loop",
  axis: "axis",
  settings: "",
  "model.json": "",
  "functions.py": "",
};

export function Center({ state }: { state: AppState }) {
  const { tabs, active, activate, close } = useStore();
  const current = tabs.find((t) => t.id === active) ?? null;
  return (
    <div className="panel">
      <div className="tabbar" role="tablist">
        <div className={`tab ${current === null ? "active" : ""}`} onClick={() => activate(null)} data-testid="tab-graph">
          Dependency graph
        </div>
        {tabs.map((tab: Tab) => (
          <div
            key={tab.id}
            className={`tab ${tab.id === active ? "active" : ""}`}
            onClick={() => activate(tab.id)}
            onAuxClick={(e) => e.button === 1 && close(tab.id)}
            data-testid={`tab-${tab.id}`}
          >
            {KIND_LABEL[tab.kind] && <span className="kind">{KIND_LABEL[tab.kind]}</span>}
            <span className="mono">{tab.name}</span>
            <button
              className="icon-btn close"
              onClick={(e) => {
                e.stopPropagation();
                close(tab.id);
              }}
              title="Close"
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <div className="panel-body" style={{ overflow: current ? "auto" : "hidden" }}>
        {current ? <TabContent key={current.id} state={state} tab={current} /> : <GraphView state={state} />}
      </div>
    </div>
  );
}
