import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useStore } from "../store";
import { api, ApiError, type Mode } from "./client";
import type { AppState, BatchResult, ModelJson, Op } from "./types";

export function useAppState() {
  return useQuery<AppState>({ queryKey: ["state"], queryFn: api.state });
}

export function useRefresh() {
  const client = useQueryClient();
  return useCallback(() => client.invalidateQueries({ queryKey: ["state"] }), [client]);
}

type Runner = (mode: Mode) => Promise<BatchResult>;

/**
 * Submit a change. Commits log and refresh; previews and proposals land in the Changes
 * panel, where a preview can be committed as is.
 */
export function useSubmit() {
  const client = useQueryClient();
  return useCallback(
    async function submit(runner: Runner, mode: Mode = "commit"): Promise<BatchResult> {
      const { log, setPreview, setBottom } = useStore.getState();
      let result: BatchResult;
      try {
        result = await runner(mode);
      } catch (e) {
        log("error", e instanceof ApiError ? e.message : String(e));
        throw e;
      }
      if (!result.ok) {
        log("error", `${result.label ?? "change"}: ${result.errors.map((e) => e.message).join("; ")}`);
      } else if (result.committed) {
        log("info", `committed: ${result.label}`);
      } else if (mode === "propose") {
        log("info", `proposed: ${result.label}`);
      }
      if (mode === "commit" && result.ok) {
        setPreview(null);
      } else {
        setPreview({ result, commit: mode === "dry_run" && result.ok ? () => submit(runner, "commit") : undefined });
        setBottom("changes");
      }
      await client.invalidateQueries({ queryKey: ["state"] });
      return result;
    },
    [client],
  );
}

/** Apply operations as one atomic batch. */
export function useApply() {
  const submit = useSubmit();
  return useCallback(
    (ops: Op[], mode: Mode = "commit", options: { label?: string; outputs?: string[] } = {}) =>
      submit((m) => api.batch(ops, m, options), mode),
    [submit],
  );
}

export function useSubmitModel() {
  const submit = useSubmit();
  return useCallback((model: ModelJson, mode: Mode = "commit") => submit((m) => api.putModel(model, m), mode), [submit]);
}

export function useSubmitFunctions() {
  const submit = useSubmit();
  return useCallback((code: string, mode: Mode = "commit") => submit((m) => api.putFunctions(code, m), mode), [submit]);
}
