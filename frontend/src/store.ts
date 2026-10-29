import { create } from "zustand";
import type { BatchResult } from "./api/types";

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

export type BottomTab = "changes" | "problems" | "terminal" | "logs";

export interface LogEntry {
  at: number;
  level: string;
  message: string;
}

interface Store {
  tabs: Tab[];
  active: string | null; // null shows the dependency graph
  selected: Selection | null;
  bottom: BottomTab;
  logs: LogEntry[];
  preview: BatchResult | null; // the latest dry-run from an editor, shown in Changes
  collapsed: string[]; // collapsed graph groups
  graphInitialised: boolean; // the overview default has been applied once
  connected: boolean;
  open: (kind: TabKind, name: string) => void;
  close: (id: string) => void;
  activate: (id: string | null) => void;
  select: (selection: Selection | null) => void;
  setBottom: (tab: BottomTab) => void;
  log: (level: string, message: string, at?: number) => void;
  setPreview: (preview: BatchResult | null) => void;
  toggleGroup: (group: string) => void;
  setCollapsed: (groups: string[]) => void;
  setConnected: (connected: boolean) => void;
  renameTab: (kind: TabKind, from: string, to: string) => void;
}

export const tabId = (kind: TabKind, name: string) => `${kind}:${name}`;

export const useStore = create<Store>((set) => ({
  tabs: [],
  active: null,
  selected: null,
  bottom: "changes",
  logs: [],
  preview: null,
  collapsed: [],
  graphInitialised: false,
  connected: false,
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
  setConnected: (connected) => set({ connected }),
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
}));
