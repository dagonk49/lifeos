import { describe, expect, it } from "vitest";

import {
  andMore,
  coveredDays,
  daysBetween,
  extraKeys,
  findSamePeriod,
  formatDecimal,
  formatLongDate,
  formatPercent,
  generatedByLabel,
  goalValueText,
  humanizeKey,
  localDateIn,
  narrativeBadge,
  narrativeNotice,
  narrativeTier,
  otherSections,
  parseCalendarDate,
  periodBounds,
  periodLabel,
  plural,
  resourceLabel,
} from "./reviews";
import type { Review } from "./types";

describe("periodLabel", () => {
  it("labels a day with its weekday", () => {
    expect(periodLabel("daily", "2026-10-11", "2026-10-11")).toBe("Dimanche 11 octobre 2026");
    expect(periodLabel("daily", "2026-10-01", "2026-10-01")).toBe("Jeudi 1er octobre 2026");
  });

  it("labels a week inside one month", () => {
    expect(periodLabel("weekly", "2026-10-05", "2026-10-11")).toBe("Semaine du 5 au 11 octobre 2026");
    expect(periodLabel("weekly", "2026-06-01", "2026-06-07")).toBe("Semaine du 1er au 7 juin 2026");
  });

  it("labels a week across two months or two years", () => {
    expect(periodLabel("weekly", "2026-09-28", "2026-10-04")).toBe(
      "Semaine du 28 septembre au 4 octobre 2026",
    );
    expect(periodLabel("weekly", "2025-12-29", "2026-01-04")).toBe(
      "Semaine du 29 décembre 2025 au 4 janvier 2026",
    );
  });

  it("labels a month", () => {
    expect(periodLabel("monthly", "2026-10-01", "2026-10-31")).toBe("Octobre 2026");
    expect(periodLabel("monthly", "2026-08-01", "2026-08-31")).toBe("Août 2026");
  });

  it("falls back on malformed dates or unknown periods", () => {
    expect(periodLabel("weekly", "bad", "2026-10-11")).toBe("bad – 2026-10-11");
    expect(periodLabel("quarterly", "2026-10-01", "2026-12-31")).toBe(
      "Du 1er octobre au 31 décembre 2026",
    );
  });
});

describe("calendar helpers", () => {
  it("rejects impossible dates", () => {
    expect(parseCalendarDate("2026-02-30")).toBeNull();
    expect(parseCalendarDate("2026-2-3")).toBeNull();
    expect(parseCalendarDate("2028-02-29")).toEqual({ year: 2028, month: 2, day: 29 });
  });

  it("formats a long date in lower case", () => {
    expect(formatLongDate("2026-10-12")).toBe("lundi 12 octobre 2026");
    expect(formatLongDate("n/a")).toBe("n/a");
  });

  it("counts days between two dates", () => {
    expect(daysBetween("2026-10-05", "2026-10-11")).toBe(6);
    expect(daysBetween("2026-10-11", "2026-10-05")).toBe(-6);
    // DST change (25 October 2026 in Europe) does not matter for calendar dates.
    expect(daysBetween("2026-10-24", "2026-10-26")).toBe(2);
    expect(daysBetween("x", "2026-10-26")).toBeNull();
  });
});

describe("periodBounds (mirror of the backend)", () => {
  it("returns the day itself for a daily review", () => {
    expect(periodBounds("daily", "2026-10-11")).toEqual({ start: "2026-10-11", end: "2026-10-11" });
  });

  it("returns Monday to Sunday for a weekly review", () => {
    expect(periodBounds("weekly", "2026-10-11")).toEqual({ start: "2026-10-05", end: "2026-10-11" });
    expect(periodBounds("weekly", "2026-10-05")).toEqual({ start: "2026-10-05", end: "2026-10-11" });
    expect(periodBounds("weekly", "2026-01-01")).toEqual({ start: "2025-12-29", end: "2026-01-04" });
  });

  it("returns the whole month for a monthly review", () => {
    expect(periodBounds("monthly", "2026-10-11")).toEqual({ start: "2026-10-01", end: "2026-10-31" });
    expect(periodBounds("monthly", "2028-02-10")).toEqual({ start: "2028-02-01", end: "2028-02-29" });
    expect(periodBounds("monthly", "2026-12-31")).toEqual({ start: "2026-12-01", end: "2026-12-31" });
  });

  it("returns null for an empty day", () => {
    expect(periodBounds("weekly", "")).toBeNull();
  });
});

describe("localDateIn and coveredDays", () => {
  it("converts an instant to the local date of a time zone", () => {
    expect(localDateIn("2026-10-10T22:30:00+00:00", "Europe/Paris")).toBe("2026-10-11");
    expect(localDateIn("2026-10-10T22:30:00Z", "UTC")).toBe("2026-10-10");
    expect(localDateIn("2026-10-10T22:30:00Z", "Nowhere/Invalid")).toBeNull();
    expect(localDateIn("not a date", "UTC")).toBeNull();
  });

  it("counts the elapsed days of a period in progress", () => {
    const period = {
      start: "2026-10-05",
      end: "2026-10-11",
      timezone: "Europe/Paris",
      computed_at: "2026-10-09T18:00:00+00:00",
      partial: true,
    };
    expect(coveredDays(period)).toEqual({ days: 5, total: 7 });
    expect(coveredDays({ ...period, partial: false })).toEqual({ days: 7, total: 7 });
    expect(coveredDays({ ...period, timezone: "Nowhere/Invalid" })).toEqual({ days: 7, total: 7 });
  });
});

describe("labels", () => {
  it("names who generated a review", () => {
    expect(generatedByLabel("user")).toBe("Par vous");
    expect(generatedByLabel("agent:coaching")).toBe("Automatique");
    expect(generatedByLabel("other")).toBe("other");
  });

  it("uses the deep tier except for daily reviews", () => {
    expect(narrativeTier("daily")).toBe("fast");
    expect(narrativeTier("weekly")).toBe("deep");
    expect(narrativeTier("monthly")).toBe("deep");
  });

  it("labels resources and unknown keys", () => {
    expect(resourceLabel("facts")).toBe("Faits (mémoire)");
    expect(resourceLabel("messages")).toBe("Conversations avec l'assistant");
    expect(resourceLabel("health_data")).toBe("Health data");
    expect(humanizeKey("due_next_7_days")).toBe("Due next 7 days");
  });

  it("gives a badge for each narrative status", () => {
    expect(narrativeBadge("generated").tone).toBe("violet");
    expect(narrativeBadge("unavailable").label).toBe("Lecture indisponible");
    expect(narrativeBadge("whatever").label).toBe("Faits seuls");
  });
});

describe("narrativeNotice", () => {
  const base = { period: "weekly" as const, generated_by: "user", narrative_error: null };

  it("explains a missing reading by its error code", () => {
    const notice = narrativeNotice({ ...base, narrative_status: "unavailable", narrative_error: "cloud_consent_required" });
    expect(notice.tone).toBe("warning");
    expect(notice.text).toContain("L'assistant IA n'est pas activé pour cet espace");
    expect(notice.settings).toBe(true);

    const quota = narrativeNotice({ ...base, narrative_status: "unavailable", narrative_error: "llm_daily_quota_reached" });
    expect(quota.text).toContain("quota gratuit du jour");
    expect(quota.settings).toBe(false);
  });

  it("has French fallbacks for unknown codes", () => {
    expect(narrativeNotice({ ...base, narrative_status: "unavailable", narrative_error: "llm_error" }).text).toContain(
      "Le fournisseur IA a renvoyé une erreur.",
    );
    expect(narrativeNotice({ ...base, narrative_status: "unavailable", narrative_error: "strange" }).text).toContain(
      "« strange »",
    );
  });

  it("explains reviews generated without reading", () => {
    expect(narrativeNotice({ ...base, narrative_status: "none" }).text).toContain("option décochée");
    expect(
      narrativeNotice({ ...base, period: "daily", generated_by: "agent:coaching", narrative_status: "none" }).text,
    ).toContain("restent locaux");
  });
});

describe("facts helpers", () => {
  it("lists unknown sections only", () => {
    expect(
      otherSections({ period: {}, omitted: [], tasks: {}, journal: {}, goals: [], health: { steps: 3 } }),
    ).toEqual(["health"]);
    expect(extraKeys({ completed: 1, streak: 4 }, ["completed"])).toEqual(["streak"]);
  });

  it("formats numbers the French way", () => {
    expect(formatDecimal("12.50")).toBe("12,5");
    expect(formatDecimal(3.456, 1)).toBe("3,5");
    expect(formatDecimal(null)).toBe("");
    expect(formatDecimal("abc")).toBe("");
    expect(formatPercent(0.456)).toBe("46 %");
  });

  it("describes goal values", () => {
    expect(goalValueText("12.50", "20", "km")).toBe("12,5 / 20 km");
    expect(goalValueText("3", null, null)).toBe("3");
    expect(goalValueText(null, "10", "livres")).toBe("Cible : 10 livres");
    expect(goalValueText(null, null, "km")).toBe("");
  });

  it("handles French plurals and truncated lists", () => {
    expect(plural(0, "entrée")).toBe("0 entrée");
    expect(plural(1, "entrée")).toBe("1 entrée");
    expect(plural(2, "entrée")).toBe("2 entrées");
    expect(andMore(15, 15)).toBe("");
    expect(andMore(16, 15)).toBe("et 1 autre");
    expect(andMore(20, 15)).toBe("et 5 autres");
  });

  it("finds the review of the same period", () => {
    const review = { id: "r1", period: "weekly", period_start: "2026-10-05" } as Review;
    expect(findSamePeriod([review], "weekly", "2026-10-08")?.id).toBe("r1");
    expect(findSamePeriod([review], "weekly", "2026-10-12")).toBeUndefined();
    expect(findSamePeriod([review], "daily", "2026-10-05")).toBeUndefined();
  });
});
