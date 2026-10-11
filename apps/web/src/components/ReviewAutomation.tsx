"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId } from "react";

import { Alert, Card, Spinner, cx } from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import { loginWithPasskey } from "@/lib/passkeys";
import { AUTOMATIC_SCHEDULE, resourceLabel } from "@/lib/reviews";
import type { ReviewAutomation } from "@/lib/types";

export function automationKey(spaceId: string) {
  return ["review-automation", spaceId] as const;
}

/** Automation state of one space (also tells whether cloud AI is allowed there). */
export function useReviewAutomation(spaceId: string) {
  return useQuery({
    queryKey: automationKey(spaceId),
    queryFn: () => api<ReviewAutomation>(`/v1/reviews/automation?space_id=${encodeURIComponent(spaceId)}`),
    enabled: Boolean(spaceId),
  });
}

export function ReviewAutomationCard({ spaceId, spaceName }: { spaceId: string; spaceName: string }) {
  const queryClient = useQueryClient();
  const labelId = useId();
  const descriptionId = useId();
  const automation = useReviewAutomation(spaceId);
  const toggle = useMutation({
    // Enabling grants the coaching agent read access: a reinforced operation (passkey).
    mutationFn: (enabled: boolean) =>
      withReauth(
        () =>
          api<ReviewAutomation>("/v1/reviews/automation", {
            method: "PUT",
            body: { space_id: spaceId, enabled },
          }),
        loginWithPasskey,
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(automationKey(spaceId), data);
      void queryClient.invalidateQueries({ queryKey: ["permissions"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const data = automation.data;
  const enabled = data?.enabled ?? false;
  const missing = data?.missing_permissions ?? [];

  return (
    <Card title={<span id={labelId}>Bilans automatiques</span>}>
      {automation.isPending ? (
        <Spinner />
      ) : automation.error ? (
        <Alert tone="error">{errorMessage(automation.error)}</Alert>
      ) : (
        <div className="flex flex-col gap-3 text-sm">
          <div className="flex items-center justify-between gap-3">
            <p>
              {enabled ? "Activés" : "Désactivés"} pour l&apos;espace <strong>{spaceName}</strong>.
            </p>
            <button
              type="button"
              role="switch"
              aria-checked={enabled}
              aria-labelledby={labelId}
              aria-describedby={descriptionId}
              disabled={toggle.isPending}
              onClick={() => toggle.mutate(!enabled)}
              className={cx(
                "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-wait disabled:opacity-60",
                enabled ? "bg-teal-700" : "bg-stone-300 dark:bg-stone-700",
              )}
            >
              <span
                aria-hidden
                className={cx(
                  "inline-block size-6 rounded-full bg-white shadow transition-transform",
                  enabled ? "translate-x-5" : "translate-x-0.5",
                )}
              />
            </button>
          </div>
          {toggle.isPending && <Spinner label={enabled ? "Désactivation…" : "Activation…"} />}

          <div id={descriptionId} className="flex flex-col gap-2">
            <div>
              <h3 className="font-medium">Calendrier (heure de Paris)</h3>
              <ul className="mt-1 flex flex-col gap-1">
                {AUTOMATIC_SCHEDULE.map((item) => (
                  <li key={item.period}>
                    <span className="font-medium">{item.when}</span> : {item.what}.
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-stone-500">
                Une notification vous prévient quand un bilan automatique est prêt.
              </p>
            </div>
            <p className="text-xs text-stone-600 dark:text-stone-400">
              Ces bilans sont établis par l&apos;agent « coaching », avec ses seules permissions. Les activer lui
              accorde un accès en lecture à vos tâches, votre journal, vos objectifs et vos faits de cet espace
              (finalité « Bilans automatiques », pour un an) : une confirmation par passkey vous sera demandée.
              Les désactiver lui retire cet accès immédiatement.
            </p>
          </div>

          {missing.length > 0 &&
            (enabled ? (
              <Alert tone="warning">
                L&apos;agent « coaching » n&apos;a pas accès à : {missing.map(resourceLabel).join(", ")}. Ces
                données manqueront aux bilans automatiques
                {missing.includes("tasks") ? " ; sans accès aux tâches, aucun bilan automatique n'est établi" : ""}.
                Désactivez puis réactivez les bilans pour lui accorder de nouveau l&apos;accès.
              </Alert>
            ) : (
              <p className="text-xs text-stone-600 dark:text-stone-400">
                Accès que l&apos;activation accordera : {missing.map(resourceLabel).join(", ")}.
              </p>
            ))}

          {data && !data.cloud_ai_enabled && (
            <Alert tone="warning">
              L&apos;assistant IA n&apos;est pas autorisé pour cet espace : les bilans automatiques ne contiendront
              que les faits, calculés localement ; la lecture de l&apos;assistant sera ignorée.{" "}
              <Link href="/settings" className="font-medium underline">
                Autoriser l&apos;IA dans les réglages
              </Link>
            </Alert>
          )}

          {toggle.error && <Alert tone="error">{errorMessage(toggle.error)}</Alert>}
        </div>
      )}
    </Card>
  );
}
