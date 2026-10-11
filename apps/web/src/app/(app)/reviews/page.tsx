"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { ReviewAutomationCard, useReviewAutomation } from "@/components/ReviewAutomation";
import { ReviewGenerateForm } from "@/components/ReviewGenerateForm";
import { ReviewView } from "@/components/ReviewView";
import { Alert, Badge, Card, Empty, PageHeader, Select, Spinner, cx } from "@/components/ui";
import { api, errorMessage, isApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { generatedByLabel, isAutomatic, narrativeBadge, PERIOD_OPTIONS, periodLabel } from "@/lib/reviews";
import { useSpace } from "@/lib/space";
import type { Review, ReviewPeriod } from "@/lib/types";

const LIMIT = 60;

export default function ReviewsPage() {
  // useSearchParams needs a Suspense boundary for `next build`.
  return (
    <Suspense fallback={<Spinner />}>
      <Reviews />
    </Suspense>
  );
}

function Reviews() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  /** `/reviews?review=<id>` opens one review (e.g. from a notification). */
  const linkId = searchParams.get("review");
  const { spaces, selected, spaceName } = useSpace();
  const [pickedSpace, setPickedSpace] = useState<string | null>(null);
  const [filter, setFilter] = useState<ReviewPeriod | "">("");
  const [selection, setSelection] = useState<{ spaceId: string; review: Review } | null>(null);
  const [linkDismissed, setLinkDismissed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const linked = useQuery({
    queryKey: ["review", linkId],
    queryFn: () => api<Review>(`/v1/reviews/${encodeURIComponent(linkId ?? "")}`),
    enabled: Boolean(linkId) && !linkDismissed,
  });
  const linkedReview = linkDismissed ? undefined : linked.data;

  // Reviews concern ONE space: the selected one, else the in-page choice (default: personal).
  const personal = spaces.find((s) => s.kind === "personal") ?? spaces[0];
  const picked = pickedSpace && spaces.some((s) => s.id === pickedSpace) ? pickedSpace : null;
  const spaceId = selected ?? picked ?? linkedReview?.space_id ?? personal?.id ?? "";

  const reviews = useQuery({
    queryKey: ["reviews", spaceId, filter],
    queryFn: () =>
      api<Review[]>(
        `/v1/reviews?space_id=${encodeURIComponent(spaceId)}${filter ? `&period=${filter}` : ""}&limit=${LIMIT}`,
      ),
    enabled: Boolean(spaceId),
  });
  const automation = useReviewAutomation(spaceId);

  const list = reviews.data;
  const chosen =
    selection && selection.spaceId === spaceId
      ? (list?.find((r) => r.id === selection.review.id) ?? selection.review)
      : undefined;
  const current = chosen ?? linkedReview ?? list?.[0];

  function reveal() {
    // Move focus (and, on a phone, the viewport) to the review that is now shown.
    requestAnimationFrame(() => headingRef.current?.focus());
  }

  function show(review: Review) {
    setSelection({ spaceId, review });
    setLinkDismissed(true);
    reveal();
  }

  function onGenerated(review: Review) {
    if (filter && filter !== review.period) setFilter("");
    show(review);
  }

  function onDeleted(id: string) {
    queryClient.setQueriesData<Review[]>({ queryKey: ["reviews"] }, (old) => old?.filter((r) => r.id !== id));
    queryClient.removeQueries({ queryKey: ["review", id] });
    void queryClient.invalidateQueries({ queryKey: ["reviews"] });
    if (selection?.review.id === id) setSelection(null);
    if (linkId === id) setLinkDismissed(true);
  }

  return (
    <>
      <PageHeader title="Bilans">
        {selected === null && spaces.length > 0 ? (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-stone-600 dark:text-stone-400">Espace du bilan</span>
            <Select
              className="w-auto py-1"
              value={spaceId}
              onChange={(e) => {
                setPickedSpace(e.target.value);
                setLinkDismissed(true);
              }}
            >
              {spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </label>
        ) : (
          spaceId && <Badge>Espace : {spaceName(spaceId)}</Badge>
        )}
      </PageHeader>
      <p className="-mt-2 mb-4 text-sm text-stone-600 dark:text-stone-400">
        Chaque bilan sépare les <strong>faits</strong>, calculés localement à partir de vos données, de la{" "}
        <strong>lecture de l&apos;assistant</strong>, qui reste une interprétation à discuter.
      </p>

      {!spaceId ? (
        <Card>
          <Empty>Aucun espace disponible.</Empty>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)] lg:grid-rows-[auto_1fr] lg:items-start">
          <div className="lg:col-start-1 lg:row-start-1">
            <ReviewGenerateForm
              key={spaceId}
              spaceId={spaceId}
              spaceName={spaceName(spaceId)}
              cloudAiEnabled={automation.data?.cloud_ai_enabled}
              reviews={list}
              onGenerated={onGenerated}
            />
          </div>

          <div className="min-w-0 lg:col-start-2 lg:row-span-2 lg:row-start-1">
            {linkId && !linkDismissed && linked.error && (
              <div className="mb-3">
                <Alert tone="warning">
                  {isApiError(linked.error, "review_not_found")
                    ? "Le bilan demandé est introuvable : il a peut-être été supprimé."
                    : errorMessage(linked.error)}
                </Alert>
              </div>
            )}
            {current ? (
              <ReviewView
                key={current.id}
                review={current}
                otherSpaceName={current.space_id !== spaceId ? spaceName(current.space_id) : null}
                headingRef={headingRef}
                onDeleted={onDeleted}
              />
            ) : reviews.isPending || (linkId && !linkDismissed && linked.isPending) ? (
              <Card>
                <Spinner />
              </Card>
            ) : (
              <Card>
                <Empty>
                  Aucun bilan à afficher. Établissez-en un avec le formulaire, ou activez les bilans automatiques.
                </Empty>
              </Card>
            )}
          </div>

          <div className="flex min-w-0 flex-col gap-4 lg:col-start-1 lg:row-start-2">
            <Card
              title="Historique"
              actions={
                <Select
                  aria-label="Filtrer par période"
                  className="w-auto py-1"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value as ReviewPeriod | "")}
                >
                  <option value="">Toutes les périodes</option>
                  {PERIOD_OPTIONS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </Select>
              }
            >
              {reviews.isPending ? (
                <Spinner />
              ) : reviews.error ? (
                <Alert tone="error">{errorMessage(reviews.error)}</Alert>
              ) : !list || list.length === 0 ? (
                <Empty>{filter ? "Aucun bilan pour cette période." : "Aucun bilan pour l'instant."}</Empty>
              ) : (
                <>
                  <ul className="-mx-2 flex flex-col gap-1">
                    {list.map((review) => {
                      const active = current?.id === review.id;
                      const badge = narrativeBadge(review.narrative_status);
                      return (
                        <li key={review.id}>
                          <button
                            type="button"
                            onClick={() => show(review)}
                            aria-current={active ? "true" : undefined}
                            className={cx(
                              "w-full rounded-md px-2 py-2 text-left transition-colors",
                              active
                                ? "bg-teal-50 ring-1 ring-teal-600/40 dark:bg-teal-950/40"
                                : "hover:bg-stone-100 dark:hover:bg-stone-800",
                            )}
                          >
                            <span className="block text-sm font-medium">
                              {periodLabel(review.period, review.period_start, review.period_end)}
                            </span>
                            <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-stone-500">
                              <Badge tone={isAutomatic(review.generated_by) ? "blue" : "teal"}>
                                {generatedByLabel(review.generated_by)}
                              </Badge>
                              <Badge tone={badge.tone}>{badge.label}</Badge>
                              {review.facts.period?.partial && <Badge tone="amber">Période en cours</Badge>}
                              <span>{formatDateTime(review.generated_at)}</span>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  {list.length >= LIMIT && (
                    <p className="mt-2 text-xs text-stone-500">
                      Seuls les {LIMIT} bilans les plus récents sont affichés : filtrez par période pour remonter plus
                      loin.
                    </p>
                  )}
                </>
              )}
            </Card>

            <ReviewAutomationCard spaceId={spaceId} spaceName={spaceName(spaceId)} />
          </div>
        </div>
      )}
    </>
  );
}
