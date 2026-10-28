import { describe, expect, it } from "vitest";

import type { NotificationItem } from "@/lib/types";

import {
  digestSummary,
  digestTimesError,
  isDigest,
  parseMaxPerDay,
  preferenceErrors,
  windowsError,
} from "./notifications";

function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "n1",
    space_id: null,
    category: "suggestion",
    title: "Suggestions regroupées",
    body: "3 suggestions",
    status: "pending",
    source_type: "digest",
    source_id: null,
    deliver_after: "2026-10-25T16:00:00Z",
    created_at: "2026-10-25T16:00:00Z",
    read_at: null,
    items_count: 3,
    ...overrides,
  };
}

describe("notification preferences", () => {
  it("accepts ordered or unordered windows that do not overlap", () => {
    expect(windowsError([])).toBeNull();
    expect(windowsError([{ start: "14:00", end: "18:00" }, { start: "08:00", end: "12:00" }])).toBeNull();
    expect(windowsError([{ start: "09:00", end: "12:00" }, { start: "12:00", end: "13:00" }])).toBeNull();
  });

  it("refuses inverted, overlapping, incomplete or too many windows", () => {
    expect(windowsError([{ start: "10:00", end: "09:00" }])).toMatch(/finir après/);
    expect(windowsError([{ start: "09:00", end: "12:00" }, { start: "11:00", end: "13:00" }])).toMatch(/chevaucher/);
    expect(windowsError([{ start: "", end: "12:00" }])).toMatch(/début et de fin/);
    const five = Array.from({ length: 5 }, (_, i) => ({ start: `0${i}:00`, end: `0${i}:30` }));
    expect(windowsError(five)).toMatch(/Au plus 4/);
  });

  it("keeps at least one distinct digest time", () => {
    expect(digestTimesError(["18:00"])).toBeNull();
    expect(digestTimesError([])).toMatch(/au moins une/);
    expect(digestTimesError(["18:00", "18:00"])).toMatch(/qu'une fois/);
    expect(digestTimesError(["24:00"])).toMatch(/HH:MM/);
  });

  it("reads the maximum per day", () => {
    expect(parseMaxPerDay("")).toBeNull();
    expect(parseMaxPerDay(" 3 ")).toBe(3);
    expect(parseMaxPerDay("0")).toBeUndefined();
    expect(parseMaxPerDay("2.5")).toBeUndefined();
    expect(parseMaxPerDay("101")).toBeUndefined();
  });

  it("lists every error of a category", () => {
    const suggestion = { category: "suggestion" as const, enabled: true, max_per_day: 3, windows: [], digest_times: [] };
    expect(preferenceErrors(suggestion, "0")).toHaveLength(2);
    expect(preferenceErrors({ ...suggestion, digest_times: ["18:00"] }, "3")).toEqual([]);
  });
});

describe("digests", () => {
  it("recognises a digest and counts its suggestions", () => {
    expect(isDigest(item())).toBe(true);
    expect(isDigest(item({ source_type: "task" }))).toBe(false);
    expect(digestSummary(item())).toBe("3 suggestions");
    expect(digestSummary(item({ items_count: 1 }))).toBe("1 suggestion");
  });
});
