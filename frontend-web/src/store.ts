import { create } from "zustand";
import type { BatchResult } from "./api/types";

export interface Preview {
  result: BatchResult;
  commit?: () => Promise<unknown>; // re-run the same change for real
}

export type TabKind =
  | "metric"
  | "table"
  | "function"
  | "analysis"
  | "source"
  | "check"
  | "circularity"
  | "axis"
  | "settings"
  | "model.json"
  | "functions.py";

export interface Tab {
  id: string;
  kind: TabKind;
  name: string;
}

export interface Selection {
  kind: TabKind;
  name: string;
}

export type BottomTab = "changes" | "problems" | "versions" | "files" | "outputs" | "logs";

export interface LogEntry {
  at: number;
  level: string;
  message: string;
}

/** Where the open project's edits stand. Every committed change is saved as a version. */
export type SaveStatus =
  | { state: "idle" }
  | { state: "saving" }
  | { state: "saved"; version: number; at: number }
  | { state: "conflict"; message: string }
  | { state: "error"; message: string };

interface Store {
  tabs: Tab[];
  active: string | null; // null shows the dependency graph
  selected: Selection | null;
  bottom: BottomTab;
  logs: LogEntry[];
  preview: Preview | null; // the latest dry-run from an editor, shown in Changes
  collapsed: string[]; // collapsed graph groups
  graphInitialised: boolean; // the overview default has been applied once
  readOnly: boolean; // the example project, or an earlier version
  save: SaveStatus;
  dirty: Record<string, boolean>; // editors with unsaved edits, by draft id
  open: (kind: TabKind, name: string) => void;
  close: (id: string) => void;
  activate: (id: string | null) => void;
  select: (selection: Selection | null) => void;
  setBottom: (tab: BottomTab) => void;
  log: (level: string, message: string, at?: number) => void;
  setPreview: (preview: Preview | null) => void;
  toggleGroup: (group: string) => void;
  setCollapsed: (groups: string[]) => void;
  renameTab: (kind: TabKind, from: string, to: string) => void;
  setReadOnly: (readOnly: boolean) => void;
  setSave: (save: SaveStatus) => void;
  setDirty: (id: string, dirty: boolean) => void;
  reset: () => void; // a different project was opened
}

export const tabId = (kind: TabKind, name: string) => `${kind}:${name}`;

const fresh = {
  tabs: [],
  active: null,
  selected: null,
  bottom: "changes" as BottomTab,
  preview: null,
  collapsed: [],
  graphInitialised: false,
  readOnly: false,
  save: { state: "idle" } as SaveStatus,
  dirty: {},
};

export const useStore = create<Store>((set) => ({
  ...fresh,
  logs: [],
  open: (kind, name) =>
    set((s) => {
      const id = tabId(kind, name);
      const tabs = s.tabs.some((t) => t.id === id) ? s.tabs : [...s.tabs, { id, kind, name }];
      return { tabs, active: id, selected: { kind, name } };
    }),
  close: (id) =>
    set((s) => {
      const index = s.tabs.findIndex((t) => t.id === id);
      const tabs = s.tabs.filter((t) => t.id !== id);
      let active = s.active;
      if (s.active === id) {
        active = tabs.length ? tabs[Math.min(index, tabs.length - 1)].id : null;
      }
      return { tabs, active };
    }),
  activate: (id) => set({ active: id }),
  select: (selected) => set({ selected }),
  setBottom: (bottom) => set({ bottom }),
  log: (level, message, at) =>
    set((s) => ({ logs: [...s.logs.slice(-499), { level, message, at: at ?? Date.now() / 1000 }] })),
  setPreview: (preview) => set({ preview }),
  toggleGroup: (group) =>
    set((s) => ({
      collapsed: s.collapsed.includes(group) ? s.collapsed.filter((g) => g !== group) : [...s.collapsed, group],
    })),
  setCollapsed: (collapsed) => set({ collapsed }),
  renameTab: (kind, from, to) =>
    set((s) => {
      const fromId = tabId(kind, from);
      const toId = tabId(kind, to);
      return {
        tabs: s.tabs.map((t) => (t.id === fromId ? { id: toId, kind, name: to } : t)),
        active: s.active === fromId ? toId : s.active,
        selected: s.selected?.kind === kind && s.selected.name === from ? { kind, name: to } : s.selected,
      };
    }),
  setReadOnly: (readOnly) => set({ readOnly }),
  setSave: (save) => set({ save }),
  setDirty: (id, dirty) =>
    set((s) => {
      if (Boolean(s.dirty[id]) === dirty) return s;
      const next = { ...s.dirty };
      if (dirty) next[id] = true;
      else delete next[id];
      return { dirty: next };
    }),
  reset: () => set({ ...fresh }),
}));

export const hasUnsavedEdits = (s: { dirty: Record<string, boolean> }) => Object.keys(s.dirty).length > 0;
