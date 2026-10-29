import type {
  AnalysisResult,
  AppState,
  BatchResult,
  FileInfo,
  ModelJson,
  Op,
  OutputInfo,
  ProjectInfo,
  RunResultJson,
  SourcePreview,
  SweepResult,
  VersionInfo,
} from "./types";

// Empty in development (Vite proxies /api) and when the app is served next to the API.
const BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/+$/, "");

export interface ErrorInfo {
  code: string;
  message: string;
  [key: string]: unknown;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public errors: ErrorInfo[] = [],
    public status = 0,
  ) {
    super(message);
  }

  get code(): string | undefined {
    return this.errors[0]?.code;
  }
}

/** The open project, and the version the UI last loaded: commits expect to build on it. */
let project = "";
let loaded: number | null = null;

export function bindProject(id: string): void {
  if (id !== project) loaded = null;
  project = id;
}

export function setLoadedVersion(version: number | null): void {
  loaded = version;
}

export const loadedVersion = () => loaded;

async function call<T>(method: string, path: string, body?: unknown, raw?: Blob): Promise<T> {
  const headers: Record<string, string> = { "X-EPM-Client": "web" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
  } catch (e) {
    throw new ApiError(`cannot reach the epm service: ${e instanceof Error ? e.message : String(e)}`);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const errors = (data?.errors ?? []) as ErrorInfo[];
    throw new ApiError(errors[0]?.message ?? `${response.status} ${response.statusText}`, errors, response.status);
  }
  return data as T;
}

const enc = encodeURIComponent;
const here = (path: string) => `/api/projects/${enc(project)}${path}`;
const query = (params: Record<string, string | number | boolean | null | undefined>) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? `?${entries.map(([k, v]) => `${k}=${enc(String(v))}`).join("&")}` : "";
};
const filePath = (path: string) => path.split("/").map(enc).join("/");

/** An absolute URL for links and downloads. */
export const url = (path: string) => BASE + path;

export type Mode = "commit" | "dry_run";

function remember<T extends { version?: number; committed?: boolean }>(result: T): T {
  if (result.committed && typeof result.version === "number") loaded = result.version;
  return result;
}

/** The open project. */
export const api = {
  state: (version?: number) => call<AppState>("GET", here(`/state${query({ version })}`)),
  batch: (ops: Op[], mode: Mode = "commit", options: { label?: string; outputs?: string[]; allow_errors?: boolean } = {}) =>
    call<BatchResult>("POST", here("/batches"), { ops, mode, ...options, expected_version: loaded }).then(remember),
  putModel: (model: ModelJson, mode: Mode = "commit") =>
    call<BatchResult>("PUT", here("/model"), { model, mode, expected_version: loaded }).then(remember),
  putFunctions: (code: string, mode: Mode = "commit") =>
    call<BatchResult>("PUT", here("/functions"), { code, mode, expected_version: loaded }).then(remember),
  run: (overrides: Record<string, unknown>, outputs?: string[], save = false) =>
    call<RunResultJson & { output?: OutputInfo }>("POST", here("/run"), { overrides, outputs, save }),
  sweep: (inputs: Record<string, number[]>, outputs: string[], mode: "grid" | "zip", overrides: Record<string, unknown> = {}, save = false) =>
    call<SweepResult & { output?: OutputInfo }>("POST", here("/sweep"), { inputs, outputs, mode, overrides, save }),
  runAnalysis: (name: string, options: { save?: boolean; plot?: boolean } = {}) =>
    call<AnalysisResult & { outputs?: OutputInfo[] }>("POST", here(`/analyses/${enc(name)}/run`), options),
  sourcePreview: (name: string) => call<SourcePreview>("GET", here(`/sources/${enc(name)}/preview`)),
  versions: () => call<VersionInfo[]>("GET", here("/versions")),
  restore: (number: number) =>
    call<{ restored: number; version: VersionInfo }>("POST", here(`/versions/${number}/restore`), { expected_version: loaded }),
  undo: () => call<{ restored: number; version: VersionInfo }>("POST", here("/undo"), { expected_version: loaded }),
  files: () => call<FileInfo[]>("GET", here("/files")),
  uploadFile: (path: string, data: Blob) =>
    call<BatchResult>("PUT", here(`/files/${filePath(path)}${query({ expected_version: loaded })}`), undefined, data).then(remember),
  deleteFile: (path: string) =>
    call<BatchResult>("DELETE", here(`/files/${filePath(path)}${query({ expected_version: loaded })}`)).then(remember),
  outputs: () => call<OutputInfo[]>("GET", here("/outputs")),
  deleteOutput: (id: string) => call<{ ok: boolean }>("DELETE", here(`/outputs/${enc(id)}`)),
  exportUrl: (version?: number) => url(here(`/export${query({ version })}`)),
  fileUrl: (path: string) => url(here(`/files/${filePath(path)}`)),
  outputUrl: (id: string) => url(here(`/outputs/${enc(id)}`)),
  plotUrl: (name: string) => url(here(`/analyses/${enc(name)}/plot.png`)),
};

/** Every project. */
export const projects = {
  list: (q?: string, deleted = false) => call<ProjectInfo[]>("GET", `/api/projects${query({ q, deleted: deleted || undefined })}`),
  get: (id: string) => call<ProjectInfo>("GET", `/api/projects/${enc(id)}`),
  create: (name: string, description = "", from?: string) => call<ProjectInfo>("POST", "/api/projects", { name, description, from }),
  importEpm: (file: Blob, name?: string) => call<ProjectInfo>("POST", `/api/projects/import${query({ name })}`, undefined, file),
  update: (id: string, fields: { name?: string; description?: string }) => call<ProjectInfo>("PATCH", `/api/projects/${enc(id)}`, fields),
  duplicate: (id: string, name?: string) => call<ProjectInfo>("POST", `/api/projects/${enc(id)}/duplicate`, { name }),
  remove: (id: string) => call<ProjectInfo>("DELETE", `/api/projects/${enc(id)}`),
  restore: (id: string) => call<ProjectInfo>("POST", `/api/projects/${enc(id)}/restore`),
  exportUrl: (id: string) => url(`/api/projects/${enc(id)}/export`),
};

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
