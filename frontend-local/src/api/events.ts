import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useStore } from "../store";
import { socketUrl } from "./client";
import type { ServerEvent } from "./types";

/** Listen to session events; refetch state when the model, history or proposals change. */
export function useServerEvents(): void {
  const client = useQueryClient();
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    const { log, setConnected } = useStore.getState();

    const connect = () => {
      socket = new WebSocket(socketUrl("/api/events"));
      socket.onopen = () => setConnected(true);
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as ServerEvent;
        if (event.type === "log" && event.message) {
          log(event.level ?? "info", event.message, event.at);
        } else if (event.type === "model_changed") {
          if (event.source && event.source !== "ui") {
            log("info", `model changed (${event.source}), version ${event.version}`, event.at);
          }
          void client.invalidateQueries({ queryKey: ["state"] });
        } else if (event.type === "history_changed" || event.type === "pending_changed") {
          if (event.type === "pending_changed") log("info", "proposed changes updated", event.at);
          void client.invalidateQueries({ queryKey: ["state"] });
        } else if (event.type === "hello") {
          void client.invalidateQueries({ queryKey: ["state"] });
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      socket?.close();
    };
  }, [client]);
}
