"use client";

import { useEffect, useId, useRef } from "react";

import { Button } from "@/components/ui";
import { maskedSummary, maskedTotal, type SendReview } from "@/lib/privacy";

/**
 * The control before sending, shown only when something is masked or left out: what stays
 * on the server, what is replaced by a label. The owner confirms, removes an attachment,
 * or cancels (the message and its attachments stay as they are).
 */
export function SendReviewPanel({
  review,
  sending,
  onConfirm,
  onCancel,
  onRemove,
}: {
  review: SendReview;
  sending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onRemove: (id: string) => void;
}) {
  const titleId = useId();
  const panel = useRef<HTMLElement>(null);
  // Read first: focus goes to the control itself, never straight to « Confirmer ».
  useEffect(() => {
    panel.current?.focus();
  }, []);
  const masked = maskedTotal(review.masked);
  const nothingLeft = masked === 0 && review.items.length === 0;

  return (
    <section
      ref={panel}
      tabIndex={-1}
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
      className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 outline-none focus-visible:ring-2 focus-visible:ring-teal-600 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
    >
      <h3 id={titleId} className="font-semibold">
        Avant l&apos;envoi à l&apos;assistant IA
      </h3>
      {masked > 0 && (
        <p className="mt-1">
          Dans votre message : {maskedSummary(review.masked)}{" "}
          {masked > 1 ? "seront remplacés" : "sera remplacé"} par une étiquette (par exemple [IBAN]). La valeur
          reste sur votre serveur.
        </p>
      )}
      {review.items.length > 0 && (
        <ul aria-label="Pièces jointes vérifiées" className="mt-1 flex flex-col gap-1">
          {review.items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-x-2">
              <span>
                <span className="font-medium">« {item.title} »</span>
                {item.sent
                  ? item.detail
                    ? ` : envoyée, identifiants masqués : ${item.detail}`
                    : " : envoyée"
                  : ` : reste sur votre serveur (${item.detail})`}
              </span>
              <button
                type="button"
                className="text-xs underline"
                aria-label={`Retirer « ${item.title} » du message`}
                onClick={() => onRemove(item.id)}
              >
                Retirer
              </button>
            </li>
          ))}
        </ul>
      )}
      {nothingLeft && <p className="mt-1">Plus rien n&apos;est masqué ni laissé de côté.</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={onConfirm} disabled={sending}>
          Confirmer l&apos;envoi
        </Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>
          Annuler
        </Button>
      </div>
    </section>
  );
}
