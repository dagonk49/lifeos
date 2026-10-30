"use client";

import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { FactCorrectionForm, useInvalidateFacts } from "@/components/MemoryReviewCorrectForm";
import { Alert, Badge, Button } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { KIND_LABELS, ORIGIN_LABELS } from "@/lib/format";
import {
  ORIGIN_TONES,
  REVIEW_DONE_MESSAGES,
  REVIEW_KIND_LABELS,
  REVIEW_KIND_TONES,
  factDateParts,
  reviewActionRequest,
  shortStatement,
  staleAgeText,
  type ReviewAction,
  type SpacedReviewItem,
} from "@/lib/memoryReview";
import type { Fact } from "@/lib/types";

function FactPanel({
  fact,
  stale,
  busy,
  correcting,
  onAction,
  onCorrect,
  onCancelCorrection,
  onCorrected,
}: {
  fact: Fact;
  stale: boolean;
  busy: boolean;
  correcting: boolean;
  onAction: (action: ReviewAction) => void;
  onCorrect: () => void;
  onCancelCorrection: () => void;
  onCorrected: () => void;
}) {
  const quote = `« ${shortStatement(fact.statement)} »`;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-stone-200 bg-stone-50 p-3 dark:border-stone-800 dark:bg-stone-950/40">
      {correcting ? (
        <FactCorrectionForm fact={fact} onCancel={onCancelCorrection} onCorrected={onCorrected} />
      ) : (
        <p className="break-words">{fact.statement}</p>
      )}
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
        <Badge tone="violet">{KIND_LABELS[fact.kind] ?? fact.kind}</Badge>
        <Badge tone={ORIGIN_TONES[fact.origin]}>{ORIGIN_LABELS[fact.origin] ?? fact.origin}</Badge>
        {factDateParts(fact).map((part) => (
          <span key={part}>· {part}</span>
        ))}
      </div>
      {stale && <p className="text-xs text-stone-600 dark:text-stone-400">{staleAgeText(fact)}</p>}
      {!correcting && (
        <div className="flex flex-wrap gap-1.5">
          {stale ? (
            <>
              <Button
                size="sm"
                disabled={busy}
                aria-label={`Toujours vrai : ${quote}`}
                onClick={() => onAction({ type: "confirm", factId: fact.id })}
              >
                Toujours vrai
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                aria-label={`Plus vrai : ${quote}`}
                onClick={() => onAction({ type: "retract", factId: fact.id })}
              >
                Plus vrai
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              aria-label={`Retirer ${quote}`}
              onClick={() => onAction({ type: "retract", factId: fact.id })}
            >
              Retirer
            </Button>
          )}
          <Button size="sm" variant="ghost" disabled={busy} aria-label={`Corriger ${quote}`} onClick={onCorrect}>
            Corriger
          </Button>
        </div>
      )}
    </div>
  );
}

/** One point of the « À vérifier » queue with its actions. */
export function MemoryReviewItemCard({
  item,
  showSpace,
  onDone,
}: {
  item: SpacedReviewItem;
  showSpace: boolean;
  /** Called with a confirmation message once the action is applied and lists refreshed. */
  onDone: (message: string) => void;
}) {
  const titleId = useId();
  const invalidate = useInvalidateFacts();
  const [correctingId, setCorrectingId] = useState<string | null>(null);
  const action = useMutation({
    mutationFn: (request: ReviewAction) => {
      const { path, body } = reviewActionRequest(request);
      return api<unknown>(path, { method: "POST", body });
    },
    // Awaited: the buttons stay disabled until the queue no longer shows this item.
    onSuccess: async (_data, request) => {
      await invalidate();
      onDone(REVIEW_DONE_MESSAGES[request.type]);
    },
  });

  const stale = item.kind === "stale";
  const [first, second] = item.facts;
  const busy = action.isPending;

  return (
    <article
      aria-labelledby={titleId}
      className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900"
    >
      <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id={titleId} className="flex flex-wrap items-center gap-2 text-base font-semibold">
            <Badge tone={REVIEW_KIND_TONES[item.kind]}>{REVIEW_KIND_LABELS[item.kind]}</Badge>
          </h2>
          <p className="text-sm text-stone-600 dark:text-stone-400">{item.reason}</p>
        </div>
        {showSpace && <Badge>{item.space_name}</Badge>}
      </header>

      <div className={stale ? "flex flex-col gap-3" : "grid gap-3 sm:grid-cols-2"}>
        {item.facts.map((fact) => (
          <FactPanel
            key={fact.id}
            fact={fact}
            stale={stale}
            busy={busy}
            correcting={correctingId === fact.id}
            onAction={(request) => action.mutate(request)}
            onCorrect={() => setCorrectingId(fact.id)}
            onCancelCorrection={() => setCorrectingId(null)}
            onCorrected={() => {
              setCorrectingId(null);
              onDone(REVIEW_DONE_MESSAGES.correct);
            }}
          />
        ))}
      </div>

      {!stale && first && second && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={busy || correctingId !== null}
            onClick={() => action.mutate({ type: "dismiss", factIds: [first.id, second.id] })}
          >
            Garder les deux
          </Button>
          <span className="text-xs text-muted">Ils ne seront plus signalés ensemble.</span>
        </div>
      )}
      {busy && <p className="mt-2 text-xs text-muted">Mise à jour…</p>}
      {action.error && (
        <div className="mt-2">
          <Alert tone="error">{errorMessage(action.error)}</Alert>
        </div>
      )}
    </article>
  );
}
