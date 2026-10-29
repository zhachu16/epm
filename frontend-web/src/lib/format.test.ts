import { describe, expect, it } from "vitest";
import { formatBytes, formatWhen } from "./format";

describe("formatBytes", () => {
  it("picks a unit", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(9688)).toBe("9.5 KB");
    expect(formatBytes(3 * 1024 ** 2)).toBe("3.0 MB");
  });
});

describe("formatWhen", () => {
  it("shows the date only for other days", () => {
    const now = new Date("2026-10-29T15:00:00");
    expect(formatWhen("2026-10-29T14:05:00", now)).not.toContain("2026");
    expect(formatWhen("2026-10-01T14:05:00", now)).toContain(new Date("2026-10-01T14:05:00").toLocaleDateString());
    expect(formatWhen(null, now)).toBe("—");
    expect(formatWhen("not a date", now)).toBe("—");
  });
});
