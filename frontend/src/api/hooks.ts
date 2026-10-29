import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useStore } from "../store";
import { api, ApiError, type Mode } from "./client";
import type { AppState, BatchResult, Op } from "./types";

export function useAppState() {
  return useQuery<AppState>({ queryKey: ["state"], queryFn: api.state });
}

/** Apply operations; previews and proposals land in the Changes panel. */
export function useApply() {
  const client = useQueryClient();
  return useCallback(
    async (ops: Op[], mode: Mode = "commit", options: { label?: string; outputs?: string[] } = {}): Promise<BatchResult> => {
      const { log, setPreview, setBottom } = useStore.getState();
      let result: BatchResult;
      try {
        result = await api.batch(ops, mode, options);
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
      if (mode !== "commit" || !result.ok) {
        setPreview(result);
        setBottom("changes");
      } else {
        setPreview(null);
      }
      await client.invalidateQueries({ queryKey: ["state"] });
      return result;
    },
    [client],
  );
}

export function useRefresh() {
  const client = useQueryClient();
  return useCallback(() => client.invalidateQueries({ queryKey: ["state"] }), [client]);
}
