// Pure helpers for the memory quality tab « À vérifier » (F07): French labels, merging
// the per-space queues, action requests. No React here so that everything is unit-testable.

import { formatDate } from "./format";
import type { Fact, MemoryReviewItem, MemoryReviewQueue, Origin, Uuid } from "./types";

export type ReviewKind = MemoryReviewItem["kind"];

/** TanStack Query key prefix of the review queues (one query per space). */
export const MEMORY_REVIEW_KEY = "memory-review";

export function memoryReviewKey(spaceId: Uuid): [string, Uuid] {
  return [MEMORY_REVIEW_KEY, spaceId];
}

/** Mirror of the backend cap (`MAX_ITEMS` in memory/quality.py), per space. */
export const MAX_REVIEW_ITEMS = 50;

export const REVIEW_KIND_LABELS: Record<ReviewKind, string> = {
  conflict: "Contradiction possible",
  duplicate: "Doublon",
  stale: "Préférence ancienne",
};

export const REVIEW_KIND_TONES = {
  conflict: "red",
  duplicate: "blue",
  stale: "amber",
} as const satisfies Record<ReviewKind, string>;

/** Badge tone of a fact's origin (declared, confirmed, extracted, inferred). */
export const ORIGIN_TONES = {
  user_declared: "teal",
  user_confirmed: "blue",
  extracted: "amber",
  inferred: "amber",
} as const satisfies Record<Origin, string>;

const KIND_ORDER: Record<ReviewKind, number> = { conflict: 0, duplicate: 1, stale: 2 };

export interface SpacedReviewItem extends MemoryReviewItem {
  /** Stable React key, unique across spaces. */
  key: string;
  space_id: Uuid;
  space_name: string;
}

export interface ReviewSource {
  spaceId: Uuid;
  spaceName: string;
  /** undefined while loading or after an error. */
  queue: MemoryReviewQueue | undefined;
}

export interface MergedReview {
  items: SpacedReviewItem[];
  /** False as soon as one loaded space has no local embedding model. */
  semantic: boolean;
  /** True when at least one space reached the server cap (more items may exist). */
  truncated: boolean;
}

/** The date a fact last proved true: its last confirmation, else its creation. */
export function factReferenceDate(fact: Fact): string {
  return fact.last_confirmed_at ?? fact.created_at;
}

function compareItems(a: SpacedReviewItem, b: SpacedReviewItem): number {
  const byKind = KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
  if (byKind !== 0) return byKind;
  if (a.kind === "stale" && b.kind === "stale") {
    // Oldest preferences first, as the server does.
    const left = a.facts[0] ? Date.parse(factReferenceDate(a.facts[0])) : 0;
    const right = b.facts[0] ? Date.parse(factReferenceDate(b.facts[0])) : 0;
    return left - right;
  }
  // Closest pairs first; an exact text match (no distance) is the closest possible.
  return (a.distance ?? 0) - (b.distance ?? 0);
}

/**
 * Merges the queues of several spaces into one list labelled with the space name:
 * contradictions first, then duplicates, then ageing preferences (stable otherwise).
 */
export function mergeReviewQueues(sources: ReviewSource[]): MergedReview {
  const items: SpacedReviewItem[] = [];
  let semantic = true;
  let truncated = false;
  for (const source of sources) {
    if (!source.queue) continue;
    if (!source.queue.semantic) semantic = false;
    if (source.queue.items.length >= MAX_REVIEW_ITEMS) truncated = true;
    for (const item of source.queue.items) {
      items.push({
        ...item,
        key: `${source.spaceId}:${item.kind}:${item.facts.map((f) => f.id).join("+")}`,
        space_id: source.spaceId,
        space_name: source.spaceName,
      });
    }
  }
  items.sort(compareItems);
  return { items, semantic, truncated };
}

/** Text of the count badge on the tab: "3", or "50+" when the server cap was reached. */
export function reviewBadgeText(count: number, truncated: boolean): string {
  return `${count}${truncated ? "+" : ""}`;
}

/** Spoken count, e.g. for the tab's accessible name: "3 éléments", "au moins 50 éléments". */
export function reviewCountText(count: number, truncated = false): string {
  if (count === 0) return "aucun élément";
  const more = truncated ? "au moins " : "";
  return `${more}${count} élément${count > 1 ? "s" : ""}`;
}

/** Date details of a fact, for the review cards and the facts list. */
export function factDateParts(fact: Fact): string[] {
  const parts: string[] = [];
  if (fact.occurred_at) parts.push(`le ${formatDate(fact.occurred_at)}`);
  parts.push(`enregistré le ${formatDate(fact.created_at)}`);
  if (fact.last_confirmed_at) parts.push(`confirmé le ${formatDate(fact.last_confirmed_at)}`);
  return parts;
}

const DAY_MS = 86_400_000;

function elapsedText(from: string, now: Date): string {
  const days = Math.max(0, Math.floor((now.getTime() - Date.parse(from)) / DAY_MS));
  if (days < 31) return "il y a moins d'un mois";
  const months = Math.floor(days / 30.44);
  if (months < 12) return `il y a ${months} mois`;
  const years = Math.floor(months / 12);
  return years === 1 ? "il y a plus d'un an" : `il y a plus de ${years} ans`;
}

/** Age of an ageing preference: "Confirmée il y a 8 mois" / "Enregistrée il y a 7 mois, jamais confirmée". */
export function staleAgeText(fact: Fact, now: Date = new Date()): string {
  if (fact.last_confirmed_at) return `Confirmée ${elapsedText(fact.last_confirmed_at, now)}`;
  return `Enregistrée ${elapsedText(fact.created_at, now)}, jamais confirmée`;
}

/** Short quote of a statement for accessible button names ("Retirer « … »"). */
export function shortStatement(statement: string, max = 60): string {
  const text = statement.replace(/\s+/g, " ").trim();
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
}

export type ReviewAction =
  | { type: "dismiss"; factIds: [Uuid, Uuid] }
  | { type: "retract"; factId: Uuid }
  | { type: "confirm"; factId: Uuid };

/** HTTP request (always POST) answering a review item. */
export function reviewActionRequest(action: ReviewAction): { path: string; body?: unknown } {
  switch (action.type) {
    case "dismiss":
      return { path: "/v1/memory/review/dismiss", body: { fact_ids: action.factIds } };
    case "retract":
      return { path: `/v1/memory/facts/${encodeURIComponent(action.factId)}/retract` };
    case "confirm":
      return { path: `/v1/memory/facts/${encodeURIComponent(action.factId)}/confirm` };
  }
}

export const REVIEW_DONE_MESSAGES: Record<ReviewAction["type"] | "correct", string> = {
  dismiss: "Les deux éléments sont conservés ; ils ne seront plus signalés ensemble.",
  retract: "Élément retiré de la mémoire.",
  confirm: "Préférence confirmée.",
  correct: "Correction enregistrée ; l'ancienne version reste dans l'historique.",
};
