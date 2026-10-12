"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useState } from "react";

import { Alert, Button, Card, Field, Input, Spinner, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, todayLocal } from "@/lib/format";
import { findSamePeriod, narrativeTier, PERIOD_OPTIONS, periodBounds, periodLabel } from "@/lib/reviews";
import type { Review, ReviewPeriod } from "@/lib/types";

export function ReviewGenerateForm({
  spaceId,
  spaceName,
  cloudAiEnabled,
  reviews,
  onGenerated,
}: {
  spaceId: string;
  spaceName: string;
  /** undefined while unknown. */
  cloudAiEnabled: boolean | undefined;
  /** Reviews already loaded for this space (to warn before replacing one). */
  reviews: Review[] | undefined;
  onGenerated: (review: Review) => void;
}) {
  const queryClient = useQueryClient();
  const helpId = useId();
  const today = todayLocal();
  const [period, setPeriod] = useState<ReviewPeriod>("weekly");
  const [day, setDay] = useState(today);
  const [withNarrative, setWithNarrative] = useState(true);

  const generate = useMutation({
    mutationFn: () =>
      api<Review>("/v1/reviews", {
        method: "POST",
        body: { space_id: spaceId, period, day: day || null, with_narrative: withNarrative },
      }),
    onSuccess: (review) => {
      void queryClient.invalidateQueries({ queryKey: ["reviews"] });
      // A narrative consumes a request of the daily AI quota shown on the dashboard.
      if (review.narrative_status !== "none") void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      onGenerated(review);
    },
  });

  const bounds = periodBounds(period, day || today);
  const inFuture = bounds !== null && bounds.start > today;
  const existing = findSamePeriod(reviews, period, day || today);
  const tier = narrativeTier(period);
  const result = generate.data;

  function change<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      generate.reset();
    };
  }

  return (
    <Card title="Établir un bilan" footer={`Espace : ${spaceName}`}>
      <form
        className="flex flex-col gap-3"
        aria-busy={generate.isPending}
        onSubmit={(e) => {
          e.preventDefault();
          if (!inFuture) generate.mutate();
        }}
      >
        <fieldset>
          <legend className="text-sm font-medium text-stone-700 dark:text-stone-300">Période</legend>
          <div className="mt-1 grid grid-cols-3 gap-1 rounded-md border border-stone-300 p-1 dark:border-stone-700">
            {PERIOD_OPTIONS.map((option) => (
              <label
                key={option.value}
                className={cx(
                  "cursor-pointer rounded px-2 py-1.5 text-center text-sm font-medium has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-teal-700",
                  period === option.value
                    ? "bg-teal-700 text-white"
                    : "text-stone-700 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800",
                )}
              >
                <input
                  type="radio"
                  name="review-period"
                  className="sr-only"
                  value={option.value}
                  checked={period === option.value}
                  onChange={() => change(setPeriod)(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
        </fieldset>

        <Field label="Jour de référence" hint="N'importe quel jour de la période voulue. Par défaut : aujourd'hui.">
          <Input type="date" value={day} max={today} onChange={(e) => change(setDay)(e.target.value)} />
        </Field>

        {bounds && (
          <p className="text-sm" aria-live="polite">
            <span className="text-stone-600 dark:text-stone-400">Période couverte : </span>
            <strong>{periodLabel(period, bounds.start, bounds.end)}</strong>
            {bounds.end >= today && !inFuture && <span className="text-stone-500"> (en cours)</span>}
          </p>
        )}
        {inFuture && <Alert tone="warning">Cette période n&apos;a pas encore commencé.</Alert>}

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 accent-teal-700"
            checked={withNarrative}
            onChange={(e) => change(setWithNarrative)(e.target.checked)}
            aria-describedby={helpId}
          />
          <span>
            <span className="font-medium">Avec la lecture de l&apos;assistant</span>
            <span id={helpId} className="mt-0.5 block text-xs text-stone-500">
              Utilise une requête Gemini de niveau « {tier} », décomptée de votre quota du jour. Seuls les faits
              calculés et quelques extraits du journal de la période sont envoyés, après masquage local des données
              sensibles. Les faits, eux, sont toujours calculés localement, sans IA.
            </span>
          </span>
        </label>

        {withNarrative && cloudAiEnabled === false && (
          <Alert tone="warning">
            L&apos;assistant IA n&apos;est pas autorisé pour cet espace : seuls les faits seront établis.{" "}
            <Link href="/settings" className="font-medium underline">
              Ouvrir les réglages
            </Link>
          </Alert>
        )}

        {existing && (
          <p className="text-xs text-stone-600 dark:text-stone-400">
            Un bilan existe déjà pour cette période (établi le {formatDateTime(existing.generated_at)}) : il sera
            remplacé.
          </p>
        )}

        <Button type="submit" disabled={generate.isPending || inFuture || !spaceId || !bounds}>
          {generate.isPending
            ? withNarrative
              ? "Calcul et rédaction…"
              : "Calcul…"
            : existing
              ? "Établir à nouveau"
              : "Établir le bilan"}
        </Button>
        {generate.isPending && (
          <Spinner
            label={
              withNarrative
                ? "Calcul des faits puis rédaction par l'assistant : cela peut prendre jusqu'à une minute."
                : "Calcul des faits…"
            }
          />
        )}
        {result && !generate.isPending && (
          <Alert tone={result.narrative_status === "unavailable" ? "warning" : "success"}>
            {result.narrative_status === "unavailable"
              ? "Bilan établi, mais sans la lecture de l'assistant : la raison est indiquée dans le bilan."
              : "Bilan établi."}
          </Alert>
        )}
        {generate.error && <Alert tone="error">{errorMessage(generate.error)}</Alert>}
      </form>
    </Card>
  );
}
