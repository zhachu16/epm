import { useSyncExternalStore } from "react";

export type Route =
  | { page: "projects" }
  | { page: "project"; id: string; version?: number }
  | { page: "docs"; guide?: string }
  | { page: "missing" };

/** The page for a location: `/`, `/p/:projectId[?version=N]` or `/docs[/:guide]`. */
export function parseRoute(pathname: string, search = ""): Route {
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts.length === 0) return { page: "projects" };
  if (parts[0] === "p" && parts.length === 2) {
    const version = Number(new URLSearchParams(search).get("version"));
    return Number.isInteger(version) && version > 0 ? { page: "project", id: parts[1], version } : { page: "project", id: parts[1] };
  }
  if (parts[0] === "docs" && parts.length <= 2) return parts[1] ? { page: "docs", guide: parts[1] } : { page: "docs" };
  return { page: "missing" };
}

export const projectPath = (id: string, version?: number) => `/p/${encodeURIComponent(id)}${version ? `?version=${version}` : ""}`;

const subscribe = (notify: () => void) => {
  window.addEventListener("popstate", notify);
  return () => window.removeEventListener("popstate", notify);
};
const snapshot = () => location.pathname + location.search;

export function useRoute(): Route {
  const where = useSyncExternalStore(subscribe, snapshot);
  const [path, search = ""] = where.split("?");
  return parseRoute(path, search ? `?${search}` : "");
}

export function navigate(to: string, replace = false): void {
  if (to === snapshot()) return;
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** A link that navigates in place, unless the user asks for a new tab. */
export function onLinkClick(to: string) {
  return (e: React.MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(to);
  };
}
