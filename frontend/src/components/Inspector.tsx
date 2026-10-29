import type { AppState, MetricInfo } from "../api/types";
import { formatNumber, headline, seriesEntries } from "../lib/format";
import { type Selection, type TabKind, useStore } from "../store";
import { Sparkline } from "./Sparkline";

export function Inspector({ state }: { state: AppState }) {
  const selected = useStore((s) => s.selected);
  return (
    <div className="panel">
      <div className="panel-header">Inspector</div>
      <div className="panel-body" data-testid="inspector">
        {!selected ? (
          <div className="empty">Select a metric in the graph or explorer to see its value, definition and dependencies.</div>
        ) : selected.kind === "metric" && state.metrics[selected.name] ? (
          <MetricInspector state={state} name={selected.name} info={state.metrics[selected.name]} />
        ) : (
          <ObjectInspector state={state} selection={selected} />
        )}
      </div>
    </div>
  );
}

function MetricLink({ name, lag }: { name: string; lag?: number | null }) {
  const open = useStore((s) => s.open);
  const note = lag === undefined || lag === 0 ? null : lag === null ? "all" : lag === 1 ? "prev" : `t-${lag}`;
  return (
    <span className="link" onClick={() => open("metric", name)} title={note === "all" ? "reads the whole series" : undefined}>
      {name}
      {note && <span className="lag">{note}</span>}
    </span>
  );
}

const KIND_LABEL: Record<MetricInfo["kind"], string> = {
  value: "input value",
  input: "input from source",
  formula: "formula",
  "formula+history": "history + formula",
};

function MetricInspector({ state, name, info }: { state: AppState; name: string; info: MetricInfo }) {
  const definition = state.model.metrics?.[name];
  const { label, value } = headline(info.value);
  const series = seriesEntries(info.value);
  const status = info.status;
  const solver = info.circularity ? state.solver[info.circularity] : undefined;

  return (
    <div className="inspector">
      <div>
        <h2>{name}</h2>
        <div className="sub">
          <span className="pill">{KIND_LABEL[info.kind]}</span>
          {info.unit && <span className="pill">{info.unit}</span>}
          <span className="pill">{info.axis ? `axis ${info.axis}` : "scalar"}</span>
          {info.scope === "forecast" && <span className="pill">forecast only</span>}
        </div>
      </div>

      <div className="field">
        <span className="field-label">Value{label ? ` (${label})` : ""}</span>
        <span className="big-value" data-testid="inspector-value">
          {formatNumber(value, info.unit)}
        </span>
        {series.length > 1 && (
          <>
            <Sparkline values={series.map(([, v]) => (typeof v === "number" ? v : null))} />
            <div className="kv">
              {series.map(([period, v]) => [
                <span key={`${period}-k`}>{period}</span>,
                <span key={`${period}-v`} className="mono">
                  {formatNumber(v, info.unit)}
                </span>,
              ])}
            </div>
          </>
        )}
      </div>

      <div className="field">
        <span className="field-label">Definition</span>
        {definition?.formula && <div className="codeblock">{definition.formula}</div>}
        {definition?.history && (
          <div className="muted">
            history from <span className="mono">{definition.history.source}:{definition.history.item}</span>, formula for forecast
            periods
          </div>
        )}
        {definition?.input && (
          <div className="codeblock">
            {definition.input.source}:{definition.input.item ?? definition.input.cell}
            {definition.input.scale && definition.input.scale !== 1 ? ` × ${definition.input.scale}` : ""}
          </div>
        )}
        {definition?.value !== undefined && (
          <div className="codeblock">
            {typeof definition.value === "number" ? definition.value : JSON.stringify(definition.value)}
          </div>
        )}
        {info.description && <div className="muted">{info.description}</div>}
      </div>

      <div className="field">
        <span className="field-label">Status</span>
        {status && (
          <div className={`message ${status.state === "ok" ? "ok" : status.state === "warning" ? "warning" : "error"}`}>
            {status.state}
            {status.message ? `: ${status.message}` : ""}
          </div>
        )}
        {solver && (
          <div className="muted">
            circularity <span className="mono">{info.circularity}</span>: {solver.converged ? "converged" : "did not converge"} in up
            to {solver.iterations} iterations
          </div>
        )}
      </div>

      <div className="field">
        <span className="field-label">Depends on</span>
        <div className="links">
          {Object.entries(info.deps).length ? (
            Object.entries(info.deps).map(([dep, lag]) => <MetricLink key={dep} name={dep} lag={lag} />)
          ) : (
            <span className="muted">nothing</span>
          )}
        </div>
      </div>
      <div className="field">
        <span className="field-label">Used by</span>
        <div className="links">
          {info.used_by.length ? info.used_by.map((u) => <MetricLink key={u} name={u} />) : <span className="muted">nothing</span>}
        </div>
      </div>

      <div className="kv">
        {info.folder && [<span key="f">folder</span>, <span key="fv">{info.folder}</span>]}
        {info.fingerprint && [
          <span key="fp">fingerprint</span>,
          <span key="fpv" className="mono">
            {info.fingerprint.slice(0, 12)}
          </span>,
        ]}
      </div>
    </div>
  );
}

const COLLECTION: Partial<Record<TabKind, keyof AppState["model"]>> = {
  table: "tables",
  analysis: "analyses",
  check: "checks",
  circularity: "circularities",
  source: "sources",
  axis: "axes",
};

function ObjectInspector({ state, selection }: { state: AppState; selection: Selection }) {
  const { kind, name } = selection;
  const collection = COLLECTION[kind];
  const definition = collection ? (state.model[collection] as Record<string, unknown> | undefined)?.[name] : undefined;
  const check = kind === "check" ? state.checks[name] : undefined;
  const fn = kind === "function" ? state.functions.find((f) => f.name === name) : undefined;
  return (
    <div className="inspector">
      <div>
        <h2>{name}</h2>
        <div className="sub">
          <span className="pill">{kind}</span>
        </div>
      </div>
      {check && (
        <div className={`message ${check.state === "passed" ? "ok" : check.severity === "warning" ? "warning" : "error"}`}>
          {check.state}
          {check.message ? `: ${check.message}` : ""}
        </div>
      )}
      {fn && <div className="codeblock">{fn.signature} → {fn.returns}</div>}
      {definition !== undefined && <div className="codeblock">{JSON.stringify(definition, null, 2)}</div>}
      {kind === "circularity" && state.solver[name] && (
        <div className="kv">
          <span>converged</span>
          <span>{String(state.solver[name].converged)}</span>
          <span>iterations</span>
          <span>{state.solver[name].iterations}</span>
        </div>
      )}
    </div>
  );
}
