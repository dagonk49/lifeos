"use client";

import { useQueries } from "@tanstack/react-query";
import { useState } from "react";

import { MemoryReviewItemCard } from "@/components/MemoryReviewItem";
import { Alert, Card, Empty, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { MAX_REVIEW_ITEMS, memoryReviewKey, mergeReviewQueues, type MergedReview } from "@/lib/memoryReview";
import { useSpace } from "@/lib/space";
import type { MemoryReviewQueue, Uuid } from "@/lib/types";

export interface MemoryReviewState extends MergedReview {
  /** True until every space answered (successfully or not). */
  isPending: boolean;
  /** Spaces whose queue could not be loaded. */
  errors: { spaceId: Uuid; spaceName: string; error: unknown }[];
  /** "Tous les espaces": every item is labelled with its space. */
  allSpaces: boolean;
}

/**
 * « À vérifier » queues: GET /v1/memory/review needs one space, so with "Tous les espaces"
 * every space is queried and the items are merged.
 */
export function useMemoryReview(): MemoryReviewState {
  const { spaces, selected, spaceName } = useSpace();
  const targets = selected
    ? [{ id: selected, name: spaceName(selected) }]
    : spaces.map((space) => ({ id: space.id, name: space.name }));
  const results = useQueries({
    queries: targets.map((space) => ({
      queryKey: memoryReviewKey(space.id),
      queryFn: () =>
        api<MemoryReviewQueue>(`/v1/memory/review?space_id=${encodeURIComponent(space.id)}`),
    })),
  });
  const merged = mergeReviewQueues(
    targets.map((space, index) => ({
      spaceId: space.id,
      spaceName: space.name,
      queue: results[index]?.data,
    })),
  );
  const errors = targets.flatMap((space, index) => {
    const error = results[index]?.error;
    return error ? [{ spaceId: space.id, spaceName: space.name, error }] : [];
  });
  return {
    ...merged,
    isPending: results.some((result) => result.isPending),
    errors,
    allSpaces: !selected,
  };
}

/** Content of the « À vérifier » tab. */
export function MemoryReviewPanel({ review }: { review: MemoryReviewState }) {
  const [message, setMessage] = useState<string | null>(null);
  const { items, semantic, truncated, isPending, errors, allSpaces } = review;
  const loadedNothing = isPending && items.length === 0;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Doublons, contradictions possibles et préférences anciennes, détectés localement sans
          appel à l&apos;IA. Vous décidez de ce qui reste en mémoire.
        </p>
        {!semantic && (
          <p className="mt-1 text-xs text-stone-500">
            Le modèle local d&apos;analyse du sens est indisponible : seuls les doublons exacts et
            les préférences anciennes sont détectés.
          </p>
        )}
        {/* Always rendered (and empty) so that screen readers announce each confirmation. */}
        <div role="status" aria-live="polite">
          {message && (
            <p className="mt-3 rounded-md border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-900 dark:border-teal-900 dark:bg-teal-950 dark:text-teal-100">
              {message}
            </p>
          )}
        </div>
      </div>
      {errors.map(({ spaceId, spaceName, error }) => (
        <Alert key={spaceId} tone="error">
          {allSpaces && spaceName ? `${spaceName} : ` : ""}
          {errorMessage(error)}
        </Alert>
      ))}
      {loadedNothing ? (
        <Spinner />
      ) : items.length === 0 ? (
        errors.length === 0 && (
          <Card>
            <Empty>Rien à vérifier.</Empty>
          </Card>
        )
      ) : (
        <ul className="flex flex-col gap-3" aria-label="Éléments à vérifier">
          {items.map((item) => (
            <li key={item.key}>
              <MemoryReviewItemCard item={item} showSpace={allSpaces} onDone={setMessage} />
            </li>
          ))}
        </ul>
      )}
      {truncated && (
        <p className="text-xs text-stone-500">
          Seuls les {MAX_REVIEW_ITEMS} premiers éléments de chaque espace sont affichés ; les
          suivants apparaîtront une fois ceux-ci traités.
        </p>
      )}
    </div>
  );
}
