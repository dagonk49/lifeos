"use client";

import { useMutation } from "@tanstack/react-query";
import { useState, type Ref } from "react";

import { ReviewFactsBlock } from "@/components/ReviewFacts";
import { ReviewNarrativeBlock } from "@/components/ReviewNarrative";
import { Alert, Badge, Button, Card } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { generatedByLabel, isAutomatic, PERIOD_OPTIONS, periodLabel } from "@/lib/reviews";
import type { Review } from "@/lib/types";

/** One review: header, facts (local) and the assistant's reading, clearly separated. */
export function ReviewView({
  review,
  otherSpaceName,
  headingRef,
  onDeleted,
}: {
  review: Review;
  /** Set when the review belongs to another space than the one in use. */
  otherSpaceName?: string | null;
  headingRef?: Ref<HTMLHeadingElement>;
  onDeleted: (id: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const label = periodLabel(review.period, review.period_start, review.period_end);
  const remove = useMutation({
    mutationFn: () => api(`/v1/reviews/${review.id}`, { method: "DELETE" }),
    onSuccess: () => onDeleted(review.id),
  });
  const periodName = PERIOD_OPTIONS.find((p) => p.value === review.period)?.label ?? review.period;

  return (
    <Card>
      <header className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-teal-700 dark:text-teal-400">
            Bilan · {periodName}
          </p>
          <h2 ref={headingRef} tabIndex={-1} className="scroll-mt-32 text-lg font-semibold outline-none">
            {label}
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <Badge tone={isAutomatic(review.generated_by) ? "blue" : "teal"}>
              {generatedByLabel(review.generated_by)}
            </Badge>
            {otherSpaceName && <Badge>Espace : {otherSpaceName}</Badge>}
            <span>Établi le {formatDateTime(review.generated_at)}</span>
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {confirming ? (
            <div role="group" aria-label="Confirmer la suppression" className="flex flex-col gap-2">
              <p className="text-sm">Supprimer ce bilan ? Vos tâches, votre journal et vos objectifs ne sont pas touchés.</p>
              <div className="flex gap-2">
                <Button size="sm" variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
                  {remove.isPending ? "Suppression…" : "Supprimer le bilan"}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setConfirming(false)} autoFocus>
                  Annuler
                </Button>
              </div>
            </div>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} aria-label={`Supprimer le bilan « ${label} »`}>
              Supprimer
            </Button>
          )}
        </div>
      </header>
      {remove.error && (
        <div className="mb-3">
          <Alert tone="error">{errorMessage(remove.error)}</Alert>
        </div>
      )}
      <div className="flex flex-col gap-4">
        <ReviewFactsBlock facts={review.facts} generatedBy={review.generated_by} />
        <ReviewNarrativeBlock review={review} />
      </div>
    </Card>
  );
}
