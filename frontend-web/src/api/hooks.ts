import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useStore } from "../store";
import { api, ApiError, errorText, loadedVersion, type Mode } from "./client";
import type { AppState, BatchResult, ModelJson, Op } from "./types";

/** The open project at its current version, or at an earlier ``version``. */
export function useAppState(projectId: string, version?: number) {
  return useQuery<AppState>({ queryKey: ["state", projectId, version ?? "current"], queryFn: () => api.state(version) });
}

/** Reload the project and the lists that hang off it. */
export function useRefresh() {
  const client = useQueryClient();
  return useCallback(async () => {
    await Promise.all(
      ["state", "versions", "files", "outputs", "project"].map((key) => client.invalidateQueries({ queryKey: [key] })),
    );
  }, [client]);
}

type Runner = (mode: Mode) => Promise<BatchResult>;

/** Record what a write did to the save status, for anything that saves a version. */
export function useSaving() {
  const refresh = useRefresh();
  return useCallback(
    async function saving<T>(write: () => Promise<T>, version: (result: T) => number | null): Promise<T> {
      const { setSave, log, save: before } = useStore.getState();
      setSave({ state: "saving" });
      try {
        const result = await write();
        const saved = version(result);
        setSave(saved === null ? before : { state: "saved", version: saved, at: Date.now() / 1000 });
        return result;
      } catch (e) {
        const message = errorText(e);
        if (e instanceof ApiError && e.code === "version_conflict") {
          setSave({ state: "conflict", message });
        } else {
          setSave({ state: "error", message });
        }
        log("error", message);
        throw e;
      } finally {
        await refresh();
      }
    },
    [refresh],
  );
}

/**
 * Submit a change. A commit saves a new version straight away; previews land in the
 * Changes panel, where a preview can be committed as is.
 */
export function useSubmit() {
  const saving = useSaving();
  return useCallback(
    async function submit(runner: Runner, mode: Mode = "commit"): Promise<BatchResult> {
      const { log, setPreview, setBottom } = useStore.getState();
      let result: BatchResult;
      try {
        result =
          mode === "commit"
            ? await saving(
                () => runner(mode),
                (r) => (r.committed ? r.version : null),
              )
            : await runner(mode);
      } catch (e) {
        // a refused request (a conflict, the read-only example) reads like a failed batch,
        // so editors show it where they show their other errors
        if (mode !== "commit") log("error", errorText(e));
        return failed(mode, e);
      }
      if (!result.ok) {
        log("error", `${result.label ?? "change"}: ${result.errors.map((e) => e.message).join("; ")}`);
      } else if (result.committed) {
        log("info", `saved version ${result.version}: ${result.label}`);
      }
      if (mode === "commit" && result.ok) {
        setPreview(null);
      } else {
        setPreview({ result, commit: mode === "dry_run" && result.ok ? () => submit(runner, "commit") : undefined });
        setBottom("changes");
      }
      return result;
    },
    [saving],
  );
}

function failed(mode: Mode, e: unknown): BatchResult {
  const code = e instanceof ApiError ? (e.code ?? "error") : "error";
  return {
    ok: false,
    mode,
    version: loadedVersion() ?? 0,
    id: null,
    label: null,
    committed: false,
    errors: [{ index: -1, op: "batch", code, message: errorText(e) }],
    changes: [],
    impact: null,
    new_errors: [],
  };
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
