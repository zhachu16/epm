// Shapes returned by the epm service (see service/api.py and epm/server/session.py).

export type Lag = number | null; // null: reads the whole series (reductions, next())

export interface ValueDict {
  axis: string | null;
  value?: number | boolean | null;
  values?: Record<string, number | boolean | null>;
}

export type MetricState = "ok" | "warning" | "error" | "blocked";

export interface MetricStatus {
  state: MetricState;
  message: string | null;
}

export type MetricKind = "value" | "input" | "formula" | "formula+history";

export interface MetricInfo {
  kind: MetricKind;
  axis: string | null;
  unit: string | null;
  scope: "all" | "forecast";
  folder: string | null;
  description: string | null;
  formula: string | null;
  deps: Record<string, Lag>;
  used_by: string[];
  status: MetricStatus | null;
  value: ValueDict | null;
  fingerprint: string | null;
  circularity: string | null;
  group: string;
}

export interface Diagnostic {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  object?: string;
  detail?: Record<string, unknown>;
}

export interface CheckResult {
  state: "passed" | "failed" | "error" | "blocked";
  severity: string;
  message: string | null;
  failing: { period: string | null; value: number | null }[];
}

export interface SolverStats {
  converged: boolean;
  iterations: number;
  residual: number | null;
  periods: Record<string, number> | null;
  message: string | null;
}

export interface Change {
  kind: string;
  name: string;
  change: "added" | "removed" | "changed" | "renamed";
  before?: unknown;
  after?: unknown;
  renamed_from?: string;
}

export interface ImpactEntry {
  before: ValueDict | null;
  after: ValueDict | null;
  status: string;
  delta?: number | Record<string, number | null> | null;
}

export interface OpError {
  index: number;
  op: string;
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export interface BatchResult {
  ok: boolean;
  mode: string;
  version: number; // the version saved by a commit, otherwise the one previewed
  id: string | null;
  label: string | null;
  committed: boolean;
  errors: OpError[];
  changes: Change[];
  impact: {
    outputs: Record<string, ImpactEntry>;
    checks: Record<string, { before: string | null; after: string | null }>;
  } | null;
  new_errors: Diagnostic[];
}

export interface Binding {
  source: string;
  item?: string;
  cell?: string;
  scale?: number;
}

export interface MetricDef {
  axis?: string;
  unit?: string;
  folder?: string;
  tags?: string[];
  description?: string;
  value?: number | Record<string, number | null>;
  input?: Binding;
  formula?: string;
  history?: Binding;
  scope?: "all" | "forecast";
}

export interface AxisDef {
  kind?: "period" | "category";
  values: (number | string)[];
  historical?: (number | string)[];
  label?: string;
}

export interface SourceDef {
  file: string;
  format?: "csv" | "xlsx";
  sheet?: string;
  range?: string;
  orientation?: "rows" | "columns";
  index?: number | string;
  labels?: number | string;
  axis?: string;
  delimiter?: string;
  folder?: string;
  description?: string;
}

export interface TableRow {
  metric?: string;
  header?: string;
  label?: string;
  style?: "normal" | "subtotal" | "total";
}

export interface TableDef {
  axis?: string;
  folder?: string;
  description?: string;
  rows: TableRow[];
}

export interface SweepInputDef {
  metric: string;
  values?: number[];
  range?: { start: number; stop: number; step?: number; num?: number };
}

export interface PlotDef {
  kind: "line" | "heatmap";
  x: string;
  y?: string | string[];
  z?: string;
  title?: string;
  format?: string;
}

export interface AnalysisDef {
  inputs: SweepInputDef[];
  outputs: string[];
  mode?: "grid" | "zip";
  overrides?: Record<string, number>;
  plot?: PlotDef;
  folder?: string;
  description?: string;
}

export interface CheckDef {
  expr: string;
  tolerance?: number;
  severity?: "error" | "warning";
  folder?: string;
  description?: string;
}

export interface CircularityDef {
  metrics: string[];
  seed?: number;
  tol?: number;
  max_iter?: number;
  damping?: number;
  folder?: string;
  description?: string;
}

export interface SettingsDef {
  name?: string;
  currency?: string;
  default_axis?: string;
  strict_units?: boolean;
  key_outputs?: string[];
}

export interface ModelJson {
  settings?: SettingsDef;
  axes?: Record<string, AxisDef>;
  sources?: Record<string, SourceDef>;
  metrics?: Record<string, MetricDef>;
  circularities?: Record<string, CircularityDef>;
  tables?: Record<string, TableDef>;
  analyses?: Record<string, AnalysisDef>;
  checks?: Record<string, CheckDef>;
}

export interface FunctionInfo {
  name: string;
  signature: string;
  returns: "series" | "scalar";
  causal: boolean;
  source: string;
}

export interface BuiltinInfo {
  name: string;
  signature: string;
  doc: string;
}

export interface ProjectInfo {
  id: string;
  name: string;
  description: string;
  is_example: boolean;
  current_version: number;
  created_at: string | null;
  updated_at: string | null;
  deleted_at: string | null;
}

export interface VersionInfo {
  project_id: string;
  number: number;
  label: string;
  source: string; // create, import, duplicate, seed, web, api, mcp
  ops: number;
  bytes: number;
  sha256: string;
  restored_from: number | null;
  created_at: string | null;
  current: boolean;
}

export interface OutputInfo {
  id: string;
  project_id: string;
  version: number;
  kind: "run" | "sweep" | "analysis" | "plot";
  name: string;
  content_type: string;
  bytes: number;
  created_at: string | null;
}

export interface FileInfo {
  name: string;
  bytes: number;
}

export interface AppState {
  version: number; // the project version this state shows
  project: ProjectInfo;
  read_only: boolean; // the example, or an earlier version
  name: string;
  model: ModelJson;
  functions_source: string;
  functions: FunctionInfo[];
  files: { name: string; bytes: number }[];
  metrics: Record<string, MetricInfo>;
  checks: Record<string, CheckResult>;
  solver: Record<string, SolverStats>;
  diagnostics: Diagnostic[];
  builtins: BuiltinInfo[];
}

export type OpKind =
  | "metric"
  | "axis"
  | "source"
  | "table"
  | "check"
  | "circularity"
  | "analysis"
  | "function"
  | "functions"
  | "file"
  | "settings";

export interface Op {
  op: "create" | "update" | "upsert" | "delete" | "rename";
  kind: OpKind;
  name?: string;
  def?: unknown;
  set?: Record<string, unknown>;
  to?: string;
  data?: string;
}

export interface SweepResult {
  inputs: string[];
  outputs: string[];
  mode: string;
  rows: {
    inputs: Record<string, number>;
    outputs: Record<string, number | null>;
    status: string;
    error: string | null;
  }[];
}

export type Chart =
  | {
      kind: "line";
      x: string;
      x_values: number[];
      series: Record<string, (number | null)[]>;
      format: string | null;
      title: string | null;
    }
  | {
      kind: "heatmap";
      x: string;
      y: string;
      z: string;
      x_values: number[];
      y_values: number[];
      values: (number | null)[][];
      format: string | null;
      title: string | null;
    };

export interface AnalysisResult {
  name: string;
  sweep: SweepResult;
  chart: Chart | null;
}

export interface RunResultJson {
  ok: boolean;
  values: Record<string, ValueDict>;
  status: Record<string, MetricStatus>;
}

export interface SourcePreview {
  periods: string[];
  items: string[];
  rows: { item: string; values: (number | string | null)[] }[];
  sheet: string;
}

