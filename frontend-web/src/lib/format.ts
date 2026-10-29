import type { ValueDict } from "../api/types";

/** Scale without binary noise, so 0.1735 * 100 rounds as 17.35 rather than 17.3499… */
const scaled = (value: number, factor: number) => Number((value * factor).toPrecision(12));

/** Format a number for display, using the metric's unit when it is a percent or multiple. */
export function formatNumber(value: unknown, unit?: string | null): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value !== "number" || Number.isNaN(value)) return String(value);
  if (!Number.isFinite(value)) return value > 0 ? "∞" : "−∞";
  const u = unit?.trim().toLowerCase();
  if (u === "%") return `${scaled(value, 100).toFixed(1)}%`;
  if (u === "x") return `${value.toFixed(2)}x`;
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  if (abs >= 1 || value === 0) return value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  return value.toPrecision(4);
}

/** Apply a Python-style format spec such as ".1%" or ",.0f" (the subset analyses use). */
export function formatSpec(value: number | null | undefined, spec?: string | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (!spec) return formatNumber(value);
  const match = /^(,)?(?:\.(\d+))?([%fegx])?$/.exec(spec);
  if (!match) return formatNumber(value);
  const [, comma, digits, type] = match;
  const precision = digits === undefined ? 6 : Number(digits);
  if (type === "%") return `${scaled(value, 100).toFixed(precision)}%`;
  if (type === "x") return `${value.toFixed(precision)}x`;
  if (comma) return value.toLocaleString("en-US", { minimumFractionDigits: precision, maximumFractionDigits: precision });
  if (type === "e") return value.toExponential(precision);
  return value.toFixed(precision);
}

/** The scalar to show for a value: the scalar itself, or a series' last non-missing entry. */
export function headline(value: ValueDict | null | undefined): { label: string | null; value: unknown } {
  if (!value) return { label: null, value: null };
  if (value.axis === null) return { label: null, value: value.value };
  const entries = Object.entries(value.values ?? {}).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return { label: null, value: null };
  const [label, last] = entries[entries.length - 1];
  return { label, value: last };
}

export function seriesEntries(value: ValueDict | null | undefined): [string, number | boolean | null][] {
  return value?.values ? Object.entries(value.values) : [];
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

/** A timestamp from the service, as local time; the date only when it is not today. */
export function formatWhen(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "—";
  const time = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return at.toDateString() === now.toDateString() ? time : `${at.toLocaleDateString()} ${time}`;
}
