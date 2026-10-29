import type {
  AnalysisResult,
  AppState,
  BatchResult,
  HistoryEntry,
  ModelJson,
  Op,
  RunResultJson,
  SourcePreview,
  SweepResult,
} from "./types";

// The server embeds the session token in index.html; `npm run dev` uses --no-auth.
const token =
  document.querySelector('meta[name="epm-token"]')?.getAttribute("content") ??
  (import.meta.env.VITE_EPM_TOKEN as string | undefined) ??
  "";

export class ApiError extends Error {
  constructor(
    message: string,
    public errors: { code: string; message: string }[] = [],
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-EPM-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const errors = (data?.errors ?? []) as { code: string; message: string }[];
    throw new ApiError(errors[0]?.message ?? `${response.status} ${response.statusText}`, errors);
  }
  return data as T;
}

export type Mode = "commit" | "dry_run" | "propose";

export const api = {
  state: () => call<AppState>("GET", "/api/state"),
  batch: (ops: Op[], mode: Mode = "commit", options: { label?: string; outputs?: string[]; allow_errors?: boolean } = {}) =>
    call<BatchResult>("POST", "/api/batches", { ops, mode, ...options }),
  putModel: (model: ModelJson, mode: Mode = "commit") => call<BatchResult>("PUT", "/api/model", { model, mode }),
  putFunctions: (code: string, mode: Mode = "commit") => call<BatchResult>("PUT", "/api/functions", { code, mode }),
  commitPending: (id: string) => call<BatchResult>("POST", `/api/pending/${id}/commit`),
  rejectPending: (id: string) => call<{ ok: boolean }>("POST", `/api/pending/${id}/reject`),
  undo: () => call<HistoryEntry>("POST", "/api/undo"),
  redo: () => call<HistoryEntry>("POST", "/api/redo"),
  rollback: (id: string) => call<{ undone: HistoryEntry[] }>("POST", `/api/rollback/${id}`),
  run: (overrides: Record<string, unknown>, outputs?: string[]) =>
    call<RunResultJson>("POST", "/api/run", { overrides, outputs }),
  sweep: (inputs: Record<string, number[]>, outputs: string[], mode: "grid" | "zip", overrides: Record<string, unknown> = {}) =>
    call<SweepResult>("POST", "/api/sweep", { inputs, outputs, mode, overrides }),
  runAnalysis: (name: string) => call<AnalysisResult>("POST", `/api/analyses/${encodeURIComponent(name)}/run`),
  sourcePreview: (name: string) => call<SourcePreview>("GET", `/api/sources/${encodeURIComponent(name)}/preview`),
};

export function socketUrl(path: string): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}${path}?token=${encodeURIComponent(token)}`;
}
