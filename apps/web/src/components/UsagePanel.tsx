"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { UsageCalls } from "@/components/UsageCalls";
import { UsageMeter } from "@/components/UsageMeter";
import { UsageSwitch } from "@/components/UsageSwitch";
import { Alert, Badge, Card, Spinner } from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import { loginWithPasskey } from "@/lib/passkeys";
import type { LLMUsage, Settings } from "@/lib/types";
import {
  ASSISTANT_STATES,
  FREE_BILLING_NOTE,
  PAID_BILLING_NOTE,
  PAUSE_EXPLANATION,
  USAGE_KEY,
  assistantState,
  billingLabel,
  costBudgets,
  formatCount,
  formatRatio,
  inFlightLabel,
  marginNote,
  modelLabel,
  quotaResetLabel,
  tierDescription,
  tierQuotas,
} from "@/lib/usage";

export function useLLMUsage() {
  return useQuery({
    queryKey: USAGE_KEY,
    queryFn: () => api<LLMUsage>("/v1/guardian/llm-usage"),
    refetchInterval: 60_000,
  });
}

function PauseControl({ paused }: { paused: boolean }) {
  const queryClient = useQueryClient();
  const labelId = useId();
  const descriptionId = useId();
  const pause = useMutation({
    // The backend does not ask for a passkey today; withReauth keeps it working if it does.
    mutationFn: (next: boolean) =>
      withReauth(
        () => api<Settings>("/v1/settings", { method: "PATCH", body: { llm_paused: next } }),
        loginWithPasskey,
      ),
    onSuccess: (settings) => {
      queryClient.setQueryData<LLMUsage>(USAGE_KEY, (old) => (old ? { ...old, paused: settings.llm_paused } : old));
      for (const key of [USAGE_KEY, ["dashboard"], ["settings"], ["audit"]]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });

  return (
    <div
      className={
        paused
          ? "rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/60"
          : "rounded-md border border-stone-200 p-3 dark:border-stone-800"
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={labelId} className="font-medium">
            Pause de l&apos;assistant IA
          </h3>
          <p id={descriptionId} className="mt-0.5 text-xs text-stone-600 dark:text-stone-400">
            {PAUSE_EXPLANATION}
          </p>
        </div>
        <UsageSwitch
          checked={paused}
          tone="amber"
          labelledBy={labelId}
          describedBy={descriptionId}
          disabled={pause.isPending}
          onChange={(next) => pause.mutate(next)}
        />
      </div>
      <div className="mt-2 flex flex-col gap-2 empty:hidden">
        {pause.isPending && <Spinner label={paused ? "Reprise de l'assistant…" : "Mise en pause…"} />}
        {paused && !pause.isPending && (
          <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
            En pause : aucun appel cloud n&apos;est effectué. Désactivez la pause pour reprendre.
          </p>
        )}
        {pause.error && <Alert tone="error">{errorMessage(pause.error)}</Alert>}
      </div>
    </div>
  );
}

function UsageDetails({ usage }: { usage: LLMUsage }) {
  const billingId = useId();
  const tiersId = useId();
  const callsId = useId();
  const paid = usage.billing === "paid";
  const quotas = tierQuotas(usage.requests_today, usage.daily_request_caps, usage.in_flight);
  const reset = quotaResetLabel(usage.quota_resets_at);
  const margin = marginNote(usage);

  return (
    <div className="flex flex-col gap-4 text-sm">
      <PauseControl paused={usage.paused} />

      {!usage.enabled && (
        <Alert tone="info">
          Aucun fournisseur IA n&apos;est configuré sur le serveur : l&apos;assistant est désactivé, le reste de
          LifeOS fonctionne normalement.
        </Alert>
      )}

      <section aria-labelledby={billingId} className="flex flex-col gap-2">
        <h3 id={billingId} className="font-medium">
          Facturation
        </h3>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Badge tone={paid ? "violet" : "teal"}>{billingLabel(usage.billing)}</Badge>
          <span className="text-stone-600 dark:text-stone-400">Fournisseur : {usage.provider}</span>
        </p>
        <p className="text-xs text-stone-600 dark:text-stone-400">{paid ? PAID_BILLING_NOTE : FREE_BILLING_NOTE}</p>
        {paid && (
          <div className="grid gap-3 sm:grid-cols-2">
            {costBudgets(usage).map((budget) => (
              <UsageMeter
                key={budget.key}
                label={budget.label}
                value={budget.text}
                used={budget.used}
                limit={budget.limit}
                ariaLabel={`${budget.label} par rapport au budget`}
              />
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby={tiersId} className="flex flex-col gap-2">
        <h3 id={tiersId} className="font-medium">
          Modèles et requêtes du jour
        </h3>
        <ul className="grid gap-3 sm:grid-cols-2">
          {quotas.map((quota) => (
            <li
              key={quota.tier}
              className="flex flex-col gap-1.5 rounded-md border border-stone-200 p-3 dark:border-stone-800"
            >
              <p className="flex flex-wrap items-baseline justify-between gap-x-2">
                <span className="font-medium">{quota.label}</span>
                <code className="break-all text-xs text-stone-600 dark:text-stone-400">
                  {modelLabel(usage.models[quota.tier])}
                </code>
              </p>
              <p className="text-xs text-stone-500">{tierDescription(quota.tier)}</p>
              {paid ? (
                <p className="text-xs text-stone-600 dark:text-stone-400">
                  {formatCount(quota.used)} requête{quota.used >= 2 ? "s" : ""} aujourd&apos;hui
                  {quota.inFlight > 0 && `, ${inFlightLabel(quota.inFlight)}`}
                </p>
              ) : (
                <UsageMeter
                  label="Requêtes du jour"
                  value={formatRatio(quota.used, quota.limit)}
                  used={quota.used}
                  limit={quota.limit}
                  ariaLabel={`Requêtes du jour, niveau ${quota.label.toLowerCase()}`}
                  hint={inFlightLabel(quota.inFlight) || undefined}
                />
              )}
            </li>
          ))}
        </ul>
        <p className="text-xs text-stone-500">
          {reset && `${reset} (heure locale). `}
          {paid
            ? "En mode payant, ce sont les budgets en dollars qui limitent les appels."
            : "Plafonds fixés par LifeOS sous le quota gratuit de Google ; seuls les appels envoyés au fournisseur comptent."}{" "}
          Un appel réserve sa part avant de partir : les appels en cours comptent déjà.
          {margin && ` ${margin}`}
        </p>
      </section>

      <UsageMeter
        label="Jetons du mois"
        value={`${formatRatio(usage.month_tokens_used, usage.month_token_budget)} jetons`}
        used={usage.month_tokens_used}
        limit={usage.month_token_budget}
        ariaLabel="Jetons du mois par rapport au budget"
        hint="Budget modifiable dans « Préférences » ; une fois atteint, les appels sont refusés jusqu'au mois suivant."
      />

      <section aria-labelledby={callsId} className="flex flex-col gap-2">
        <h3 id={callsId} className="font-medium">
          Derniers appels
        </h3>
        <UsageCalls calls={usage.recent_calls} paid={paid} />
      </section>
    </div>
  );
}

/** Settings card: emergency stop, billing mode, quotas, costs and recent calls. */
export function UsagePanel({ className }: { className?: string }) {
  const usage = useLLMUsage();
  const state = usage.data ? ASSISTANT_STATES[assistantState(usage.data)] : null;
  return (
    <Card
      title="Assistant IA"
      actions={state && <Badge tone={state.tone}>{state.label}</Badge>}
      className={className}
    >
      {usage.isPending ? (
        <Spinner />
      ) : usage.error ? (
        <Alert tone="error">{errorMessage(usage.error)}</Alert>
      ) : (
        <UsageDetails usage={usage.data} />
      )}
    </Card>
  );
}
