import { describe, expect, it } from "vitest";

import {
  addDays,
  dayLabel,
  eventDays,
  groupByDay,
  mondayOf,
  rangeQuery,
  responseLabel,
  sourceErrorMessage,
  timeLabel,
  weekDays,
  weekLabel,
} from "./calendar";
import type { CalendarEvent } from "./types";

function event(overrides: Partial<CalendarEvent>): CalendarEvent {
  return {
    id: "e1",
    space_id: "s1",
    source_id: "src1",
    source_name: "Agenda",
    synced_at: "2026-10-20T08:00:00Z",
    summary: "Réunion",
    description: null,
    location: null,
    all_day: false,
    starts_at: "2026-10-22T09:00:00+02:00",
    ends_at: "2026-10-22T10:00:00+02:00",
    start_date: null,
    end_date: null,
    timezone: "Europe/Paris",
    status: "confirmed",
    busy: true,
    recurring: false,
    response: null,
    ...overrides,
  };
}

describe("calendar helpers", () => {
  it("computes weeks from Monday, across months and the end of summer time", () => {
    expect(mondayOf("2026-10-25")).toBe("2026-10-19"); // a Sunday
    expect(mondayOf("2026-10-19")).toBe("2026-10-19");
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(weekDays("2026-10-26")).toEqual([
      "2026-10-26",
      "2026-10-27",
      "2026-10-28",
      "2026-10-29",
      "2026-10-30",
      "2026-10-31",
      "2026-11-01",
    ]);
    expect(weekLabel("2026-10-26")).toBe("Semaine du 26 octobre au 1 novembre 2026");
    expect(weekLabel("2026-10-19")).toBe("Semaine du 19 au 25 octobre 2026");
    expect(dayLabel("2026-10-25")).toBe("dimanche 25 octobre");
  });

  it("asks for the window in the owner's zone (no offset sent)", () => {
    expect(rangeQuery("2026-10-19", 7)).toBe(
      "/v1/calendar/events?start=2026-10-19T00%3A00%3A00&end=2026-10-26T00%3A00%3A00",
    );
    expect(rangeQuery("2026-10-19", 1, "s1")).toContain("space_id=s1");
  });

  it("places timed and all-day occurrences on their local days", () => {
    expect(eventDays(event({}))).toEqual(["2026-10-22"]);
    // The local day comes from the string (server zone), not from the browser's zone.
    expect(eventDays(event({ starts_at: "2026-10-22T23:30:00+02:00", ends_at: "2026-10-23T00:30:00+02:00" }))).toEqual([
      "2026-10-22",
      "2026-10-23",
    ]);
    expect(eventDays(event({ starts_at: "2026-10-22T22:00:00+02:00", ends_at: "2026-10-23T00:00:00+02:00" }))).toEqual([
      "2026-10-22",
    ]);
    const holiday = event({
      all_day: true,
      starts_at: null,
      ends_at: null,
      start_date: "2026-10-31",
      end_date: "2026-11-02",
    });
    expect(eventDays(holiday)).toEqual(["2026-10-31", "2026-11-01"]);
  });

  it("groups by day, all-day first then by start", () => {
    const days = weekDays("2026-10-19");
    const late = event({ id: "late", starts_at: "2026-10-22T15:00:00+02:00", ends_at: "2026-10-22T16:00:00+02:00" });
    const early = event({ id: "early" });
    const allDay = event({
      id: "all",
      all_day: true,
      starts_at: null,
      ends_at: null,
      start_date: "2026-10-22",
      end_date: "2026-10-23",
    });
    const groups = groupByDay([late, early, allDay], days);
    expect(groups.get("2026-10-22")?.map((e) => e.id)).toEqual(["all", "early", "late"]);
    expect(groups.get("2026-10-23")).toEqual([]);
    expect([...groups.keys()]).toEqual(days);
  });

  it("labels times as written by the server", () => {
    expect(timeLabel(event({}), "2026-10-22")).toBe("09:00 – 10:00");
    // After the end of summer time, the same meeting is still at 09:00.
    expect(
      timeLabel(event({ starts_at: "2026-10-29T09:00:00+01:00", ends_at: "2026-10-29T10:00:00+01:00" }), "2026-10-29"),
    ).toBe("09:00 – 10:00");
    const overnight = event({ starts_at: "2026-10-22T22:00:00+02:00", ends_at: "2026-10-23T02:00:00+02:00" });
    expect(timeLabel(overnight, "2026-10-22")).toBe("à partir de 22:00");
    expect(timeLabel(overnight, "2026-10-23")).toBe("jusqu'à 02:00");
    expect(timeLabel(event({ all_day: true }), "2026-10-22")).toBe("Toute la journée");
  });

  it("explains answers and sync errors in French", () => {
    expect(responseLabel(event({ response: "needsAction" }))).toBe("Réponse en attente");
    expect(responseLabel(event({ response: "accepted" }))).toBeNull();
    expect(sourceErrorMessage("consent_missing")).toMatch(/non autorisée dans cet espace/);
    expect(sourceErrorMessage("http_503")).toMatch(/injoignable/);
    expect(sourceErrorMessage(null)).toBeNull();
  });
});
