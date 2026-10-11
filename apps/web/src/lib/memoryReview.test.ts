import { describe, expect, it } from "vitest";

import { formatDate } from "./format";
import {
  MAX_REVIEW_ITEMS,
  REVIEW_KIND_LABELS,
  factDateParts,
  memoryReviewKey,
  mergeReviewQueues,
  reviewActionRequest,
  reviewBadgeText,
  reviewCountText,
  shortStatement,
  staleAgeText,
} from "./memoryReview";
import type { Fact, MemoryReviewItem, MemoryReviewQueue } from "./types";

function fact(id: string, overrides: Partial<Fact> = {}): Fact {
  return {
    id,
    space_id: "s1",
    kind: "preference",
    statement: `Énoncé ${id}`,
    origin: "user_declared",
    confidence: "1.00",
    status: "active",
    occurred_at: null,
    source_type: "manual",
    source_id: null,
    supersedes_id: null,
    last_confirmed_at: null,
    created_at: "2026-01-10T08:00:00Z",
    updated_at: "2026-01-10T08:00:00Z",
    ...overrides,
  };
}

function pair(kind: "conflict" | "duplicate", a: string, b: string, distance: number | null): MemoryReviewItem {
  return { kind, reason: "raison", facts: [fact(a), fact(b)], distance };
}

function stale(id: string, created: string, confirmed: string | null = null): MemoryReviewItem {
  return {
    kind: "stale",
    reason: "Préférence ancienne : est-elle toujours vraie ?",
    facts: [fact(id, { created_at: created, last_confirmed_at: confirmed })],
    distance: null,
  };
}

function queue(items: MemoryReviewItem[], semantic = true): MemoryReviewQueue {
  return { items, semantic };
}

describe("labels", () => {
  it("names every kind in French", () => {
    expect(REVIEW_KIND_LABELS).toEqual({
      conflict: "Contradiction possible",
      duplicate: "Doublon",
      stale: "Préférence ancienne",
    });
  });

  it("uses one query key per space under a common prefix", () => {
    expect(memoryReviewKey("s1")).toEqual(["memory-review", "s1"]);
  });
});

describe("mergeReviewQueues", () => {
  it("labels items with their space and sorts conflicts, duplicates then old preferences", () => {
    const merged = mergeReviewQueues([
      {
        spaceId: "perso",
        spaceName: "Personnel",
        queue: queue([pair("duplicate", "a", "b", 0.05), stale("p1", "2025-06-01T00:00:00Z")]),
      },
      {
        spaceId: "pro",
        spaceName: "Professionnel",
        queue: queue([
          pair("conflict", "c", "d", 0.2),
          pair("conflict", "e", "f", 0.1),
          pair("duplicate", "g", "h", null),
          stale("p2", "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        ]),
      },
    ]);
    expect(merged.items.map((item) => `${item.kind}:${item.facts[0]?.id}:${item.space_name}`)).toEqual([
      "conflict:e:Professionnel",
      "conflict:c:Professionnel",
      "duplicate:g:Professionnel",
      "duplicate:a:Personnel",
      "stale:p2:Professionnel",
      "stale:p1:Personnel",
    ]);
    expect(merged.items[0]?.space_id).toBe("pro");
    expect(merged.semantic).toBe(true);
    expect(merged.truncated).toBe(false);
  });

  it("gives every item a unique key across spaces", () => {
    const same = queue([pair("duplicate", "a", "b", null)]);
    const merged = mergeReviewQueues([
      { spaceId: "s1", spaceName: "Un", queue: same },
      { spaceId: "s2", spaceName: "Deux", queue: same },
    ]);
    expect(new Set(merged.items.map((item) => item.key)).size).toBe(2);
  });

  it("skips spaces not loaded yet and reports a missing semantic model", () => {
    const merged = mergeReviewQueues([
      { spaceId: "s1", spaceName: "Un", queue: undefined },
      { spaceId: "s2", spaceName: "Deux", queue: queue([stale("p", "2025-01-01T00:00:00Z")], false) },
    ]);
    expect(merged.items).toHaveLength(1);
    expect(merged.semantic).toBe(false);
  });

  it("is semantic and empty when nothing is loaded", () => {
    expect(mergeReviewQueues([])).toEqual({ items: [], semantic: true, truncated: false });
  });

  it("flags a space that reached the server cap", () => {
    const items = Array.from({ length: MAX_REVIEW_ITEMS }, (_, i) => stale(`p${i}`, "2025-01-01T00:00:00Z"));
    expect(mergeReviewQueues([{ spaceId: "s", spaceName: "S", queue: queue(items) }]).truncated).toBe(true);
  });
});

describe("counts", () => {
  it("formats the badge and its spoken form", () => {
    expect(reviewBadgeText(3, false)).toBe("3");
    expect(reviewBadgeText(50, true)).toBe("50+");
    expect(reviewCountText(0)).toBe("aucun élément");
    expect(reviewCountText(1)).toBe("1 élément");
    expect(reviewCountText(4)).toBe("4 éléments");
    expect(reviewCountText(50, true)).toBe("au moins 50 éléments");
  });
});

describe("fact details", () => {
  it("lists the occurrence, creation and confirmation dates", () => {
    const f = fact("x", {
      occurred_at: "2026-03-02T10:00:00Z",
      last_confirmed_at: "2026-09-01T10:00:00Z",
    });
    expect(factDateParts(f)).toEqual([
      `le ${formatDate("2026-03-02T10:00:00Z")}`,
      `enregistré le ${formatDate("2026-01-10T08:00:00Z")}`,
      `confirmé le ${formatDate("2026-09-01T10:00:00Z")}`,
    ]);
    expect(factDateParts(fact("y"))).toHaveLength(1);
  });

  it("describes the age of an old preference", () => {
    const now = new Date("2026-10-11T12:00:00Z");
    expect(staleAgeText(fact("a", { created_at: "2026-02-01T00:00:00Z" }), now)).toBe(
      "Enregistrée il y a 8 mois, jamais confirmée",
    );
    expect(
      staleAgeText(fact("b", { last_confirmed_at: "2025-09-01T00:00:00Z" }), now),
    ).toBe("Confirmée il y a plus d'un an");
    expect(staleAgeText(fact("c", { created_at: "2023-01-01T00:00:00Z" }), now)).toBe(
      "Enregistrée il y a plus de 3 ans, jamais confirmée",
    );
    expect(staleAgeText(fact("d", { created_at: "2026-10-01T00:00:00Z" }), now)).toBe(
      "Enregistrée il y a moins d'un mois, jamais confirmée",
    );
  });

  it("shortens long statements for button names", () => {
    expect(shortStatement("  Je   préfère le thé ")).toBe("Je préfère le thé");
    const long = shortStatement("a".repeat(100), 20);
    expect(long).toHaveLength(20);
    expect(long.endsWith("…")).toBe(true);
  });
});

describe("reviewActionRequest", () => {
  it("maps each answer to its endpoint", () => {
    expect(reviewActionRequest({ type: "dismiss", factIds: ["a", "b"] })).toEqual({
      path: "/v1/memory/review/dismiss",
      body: { fact_ids: ["a", "b"] },
    });
    expect(reviewActionRequest({ type: "retract", factId: "a" })).toEqual({
      path: "/v1/memory/facts/a/retract",
    });
    expect(reviewActionRequest({ type: "confirm", factId: "a" })).toEqual({
      path: "/v1/memory/facts/a/confirm",
    });
  });
});
