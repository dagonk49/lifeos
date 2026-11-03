import { describe, expect, it } from "vitest";

import {
  CARD_IDS,
  daySummary,
  itemSource,
  itemTime,
  mergeLayout,
  moveCard,
  sourceFooter,
  sourceHref,
  taskSourceLabel,
  toggleCard,
} from "./dashboard";
import type { AgendaItem } from "./types";

function item(partial: Partial<AgendaItem>): AgendaItem {
  return {
    kind: "event",
    title: "Point",
    day: "2026-10-25",
    start: null,
    end: null,
    all_day: false,
    space_id: "s1",
    source_type: "calendar_event",
    source_id: "e1",
    source_label: "Perso",
    href: "/agenda",
    synced_at: null,
    detail: null,
    tentative: false,
    blocked: false,
    ...partial,
  };
}

describe("layout", () => {
  it("keeps the stored order, drops unknown and repeated cards, appends the missing ones", () => {
    const merged = mergeLayout([
      { id: "budget", visible: false },
      { id: "horoscope", visible: true },
      { id: "budget", visible: true },
      { id: "agenda" },
    ]);
    expect(merged.slice(0, 2)).toEqual([
      { id: "budget", visible: false },
      { id: "agenda", visible: true },
    ]);
    expect(merged.map((c) => c.id)).toEqual(["budget", "agenda", ...CARD_IDS.filter((id) => id !== "budget" && id !== "agenda")]);
    expect(merged.slice(2).every((c) => c.visible)).toBe(true);
    expect(mergeLayout(undefined).map((c) => c.id)).toEqual(CARD_IDS);
  });

  it("moves a card up or down, never past the edges", () => {
    const cards = mergeLayout([]);
    expect(moveCard(cards, 1, -1).slice(0, 2).map((c) => c.id)).toEqual(["agenda", "priorities"]);
    expect(moveCard(cards, 0, 1).slice(0, 2).map((c) => c.id)).toEqual(["agenda", "priorities"]);
    expect(moveCard(cards, 0, -1)).toEqual(cards);
    expect(moveCard(cards, cards.length - 1, 1)).toEqual(cards);
    expect(toggleCard(cards, "memory", false).find((c) => c.id === "memory")?.visible).toBe(false);
  });
});

describe("agenda lines", () => {
  it("reads the local time from the string, across the change of time", () => {
    expect(itemTime(item({ start: "2026-10-25T23:30:00+01:00", end: "2026-10-26T00:30:00+01:00" }))).toBe(
      "à partir de 23:30",
    );
    expect(itemTime(item({ start: "2026-10-25T09:30:00+01:00", end: "2026-10-25T10:00:00+01:00" }))).toBe(
      "09:30 – 10:00",
    );
    expect(itemTime(item({ day: "2026-10-26", start: "2026-10-25T23:30:00+01:00", end: "2026-10-26T00:30:00+01:00" }))).toBe(
      "jusqu'à 00:30",
    );
    expect(itemTime(item({ all_day: true }))).toBe("Toute la journée");
    expect(itemTime(item({ kind: "task", start: "2026-10-25T18:00:00+01:00" }))).toBe("Échéance 18:00");
  });

  it("says where each line comes from, never for another space", () => {
    expect(itemSource(item({ synced_at: "2026-10-25T08:00:00Z" }))).toMatch(/^Source : Perso · lu le /);
    expect(itemSource(item({ kind: "busy", source_label: null }))).toBe("Un autre de vos espaces (détails masqués)");
    expect(itemSource(item({ kind: "milestone", source_label: "Courir" }))).toBe("Objectif : Courir");
  });

  it("summarises a day by kind", () => {
    expect(daySummary([])).toBe("Rien de prévu");
    expect(daySummary([item({}), item({ kind: "busy" }), item({ kind: "task" })])).toBe("2 événements · 1 tâche");
    expect(daySummary([item({ kind: "payment" })])).toBe("1 paiement prévu");
  });
});

describe("sources", () => {
  it("links each origin to its page", () => {
    expect(sourceHref("review", "r 1")).toBe("/reviews?review=r%201");
    expect(sourceHref("goal", "g")).toBe("/goals?id=g");
    expect(sourceHref("journal_entry", "j")).toBe("/journal");
    expect(sourceHref("message", "m")).toBe("/chat");
    expect(sourceHref("manual", null)).toBeNull();
    expect(taskSourceLabel("journal_entry")).toBe("Journal");
    expect(taskSourceLabel("manual")).toBeNull();
  });

  it("builds footers from what the figures rest on", () => {
    expect(sourceFooter(undefined, "Calculé localement")).toBe("Calculé localement");
    expect(sourceFooter({ origin: "calendar", as_of: null, count: 1 }, "")).toBe(
      "1 agenda suivi, pas encore lus · heures dans votre fuseau",
    );
    expect(sourceFooter({ origin: "review", as_of: "2026-10-20T08:00:00Z", count: 2 }, "")).toMatch(
      /^D'après vos bilans enregistrés \(le plus récent : .*\) · aucun appel à l'IA$/,
    );
    expect(sourceFooter({ origin: "local", as_of: "2026-10-20T08:00:00Z" }, "")).toMatch(/^Calculé localement le /);
  });
});
