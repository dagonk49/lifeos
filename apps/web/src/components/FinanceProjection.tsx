"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Badge, Card, Empty, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import {
  BASELINE_CHOICES,
  BASIS_HINTS,
  BASIS_LABELS,
  BASIS_TONES,
  financeErrorMessage,
  financeKeys,
  formatMoney,
  monthLabel,
  PROJECTION_HORIZONS,
  projectionPath,
  rangeText,
} from "@/lib/finance";
import { formatDate } from "@/lib/format";
import type { Basis, FinanceProjection as Projection, ProjectionMonth, SavingsGoalProjection } from "@/lib/types";

function BasisBadge({ basis }: { basis: Basis }) {
  return <Badge tone={BASIS_TONES[basis]}>{BASIS_LABELS[basis]}</Badge>;
}

function Amounts({ month, direction, currency }: { month: ProjectionMonth; direction: "income" | "expense"; currency: string }) {
  const income = direction === "income";
  const known = income ? month.known_income : month.known_expenses;
  const scheduled = income ? month.scheduled_income : month.scheduled_expenses;
  const estimated = income ? month.estimated_income : month.estimated_expenses;
  const low = income ? month.estimated_income_low : month.estimated_expenses_low;
  const high = income ? month.estimated_income_high : month.estimated_expenses_high;
  const parts: [Basis, string][] = [];
  if (known !== "0.00") parts.push(["known", formatMoney(known, currency)]);
  if (scheduled !== "0.00") parts.push(["scheduled", formatMoney(scheduled, currency)]);
  if (estimated !== "0.00" || high !== "0.00") {
    const range = rangeText(low, high, currency);
    parts.push(["estimated", `${formatMoney(estimated, currency)}${range.startsWith("entre") ? ` (${range})` : ""}`]);
  }
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-stone-500">{income ? "Revenus" : "Dépenses"}</span>
      {parts.length === 0 ? (
        <span className="text-stone-500">aucun</span>
      ) : (
        parts.map(([basis, text]) => (
          <span key={basis} className="flex flex-wrap items-center gap-1.5 tabular-nums">
            <BasisBadge basis={basis} /> {text}
          </span>
        ))
      )}
    </div>
  );
}

function MonthRow({ month, currency }: { month: ProjectionMonth; currency: string }) {
  const label = monthLabel(month.month);
  return (
    <li className="flex flex-col gap-2 border-b border-stone-100 py-3 text-sm last:border-0 dark:border-stone-800">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold capitalize">
          {label}
          {month.state === "current" && <span className="ml-2 text-xs font-normal text-stone-500">(mois en cours)</span>}
        </h3>
        <span className="tabular-nums">
          Solde estimé <strong>{formatMoney(month.balance, currency, "always")}</strong>{" "}
          <span className="text-stone-500">({rangeText(month.balance_low, month.balance_high, currency)})</span>
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Amounts month={month} direction="income" currency={currency} />
        <Amounts month={month} direction="expense" currency={currency} />
      </div>
      {month.items.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-stone-600 dark:text-stone-400">Détail de {label}</summary>
          <ul className="mt-1 flex flex-col gap-1">
            {month.items.map((item, index) => (
              <li key={`${item.source}-${item.recurring_id ?? item.category_id ?? index}-${item.direction}`} className="flex flex-wrap items-center gap-1.5">
                <BasisBadge basis={item.basis} />
                <span className="break-words">{item.label}</span>
                <span className="text-xs text-stone-500">{item.direction === "income" ? "revenu" : "dépense"}</span>
                <span className="ml-auto tabular-nums">
                  {item.basis === "estimated" ? rangeText(item.low, item.high, currency) || formatMoney(item.amount, currency) : formatMoney(item.amount, currency)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function etaText(goal: SavingsGoalProjection): string {
  if (goal.reached) return "Objectif atteint.";
  if (!goal.eta_month) return "Date non estimable : aucun versement déclaré et solde prévu nul ou négatif.";
  const eta = monthLabel(goal.eta_month);
  const range =
    goal.eta_early && goal.eta_late && goal.eta_early !== goal.eta_late
      ? ` (entre ${monthLabel(goal.eta_early)} et ${monthLabel(goal.eta_late)})`
      : goal.eta_late === null
        ? " (peut-être jamais si le solde reste au plus bas)"
        : "";
  return `Atteint vers ${eta}${range}.`;
}

/** Savings goals: what is saved is known (last value recorded), the date is estimated. */
export function SavingsGoals({ goals, currency }: { goals: SavingsGoalProjection[]; currency: string }) {
  return (
    <section aria-label="Objectifs d'épargne" className="mt-4 border-t border-stone-100 pt-3 dark:border-stone-800">
      <h3 className="mb-2 text-sm font-semibold">Objectifs d&apos;épargne</h3>
      <ul className="flex flex-col gap-3 text-sm">
        {goals.map((goal) => (
          <li key={goal.goal_id} className="flex flex-col gap-1">
            <a href={`/goals?id=${encodeURIComponent(goal.goal_id)}`} className="font-medium underline-offset-2 hover:underline">
              {goal.title}
            </a>
            <span className="flex flex-wrap items-center gap-1.5 tabular-nums">
              <BasisBadge basis="known" /> {formatMoney(goal.saved, currency)} épargnés sur {formatMoney(goal.target, currency)}
              {goal.saved_at ? ` (relevé le ${formatDate(goal.saved_at)})` : ""}
            </span>
            <span className="flex flex-wrap items-center gap-1.5">
              <BasisBadge basis="estimated" /> {etaText(goal)}
              {goal.contribution &&
                (goal.contribution_source === "declared"
                  ? ` Versement déclaré : ${formatMoney(goal.contribution, currency)} par mois.`
                  : ` Versement supposé : le solde moyen prévu, ${formatMoney(goal.contribution, currency)} par mois.`)}
            </span>
            {goal.on_track !== null && !goal.reached && (
              <span className={goal.on_track ? "text-teal-800 dark:text-teal-300" : "text-amber-800 dark:text-amber-300"}>
                {goal.on_track
                  ? "Dans les temps pour l'échéance."
                  : `En retard sur l'échéance du ${formatDate(goal.due_date)} à ce rythme.`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Task 26: the coming months computed locally (never by the AI), every amount tagged
 * « connu », « prévu » or « estimé », with ranges, assumptions and warnings. Recomputed by
 * the server on every read: any correction shows at once (its key is invalidated).
 */
export function FinanceProjection({ spaceId }: { spaceId: string }) {
  const [months, setMonths] = useState<number>(6);
  const [baseline, setBaseline] = useState<number>(3);
  const projection = useQuery({
    queryKey: financeKeys.projection(spaceId, months, baseline),
    queryFn: () => api<Projection>(projectionPath(spaceId, months, baseline)),
  });
  const data = projection.data;

  return (
    <Card title="Projection des prochains mois">
      <p className="mb-3 text-xs text-stone-600 dark:text-stone-400">
        Calculée sur ce serveur à partir de vos opérations et de vos échéances : une estimation, pas un conseil.{" "}
        {(Object.keys(BASIS_LABELS) as Basis[]).map((basis) => (
          <span key={basis} className="mr-2 inline-flex items-center gap-1">
            <BasisBadge basis={basis} /> {BASIS_HINTS[basis]}
          </span>
        ))}
      </p>
      <div className="mb-3 flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-stone-600 dark:text-stone-400">Horizon</span>
          <Select className="w-auto py-1" value={String(months)} onChange={(e) => setMonths(Number(e.target.value))}>
            {PROJECTION_HORIZONS.map((n) => (
              <option key={n} value={n}>
                {n} mois
              </option>
            ))}
          </Select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-stone-600 dark:text-stone-400">Moyenne sur</span>
          <Select className="w-auto py-1" value={String(baseline)} onChange={(e) => setBaseline(Number(e.target.value))}>
            {BASELINE_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n === 1 ? "le dernier mois" : `les ${n} derniers mois`}
              </option>
            ))}
          </Select>
        </label>
      </div>
      {projection.isPending ? (
        <Spinner label="Calcul de la projection…" />
      ) : !data ? (
        <Alert tone="error">{financeErrorMessage(projection.error)}</Alert>
      ) : (
        <>
          {data.warnings.length > 0 && (
            <div className="mb-3 flex flex-col gap-2">
              {data.warnings.map((warning) => (
                <Alert key={`${warning.code}-${warning.label}`} tone="warning">
                  {warning.label}
                </Alert>
              ))}
            </div>
          )}
          {data.months.length === 0 ? (
            <Empty>Rien à projeter pour l&apos;instant.</Empty>
          ) : (
            <ul aria-label="Mois projetés">
              {data.months.map((month) => (
                <MonthRow key={month.month} month={month} currency={data.currency} />
              ))}
            </ul>
          )}
          {data.savings_goals && data.savings_goals.length > 0 && (
            <SavingsGoals goals={data.savings_goals} currency={data.currency} />
          )}
          <details className="mt-3 text-sm" open>
            <summary className="cursor-pointer font-medium">Hypothèses</summary>
            <ul aria-label="Hypothèses" className="mt-1 list-inside list-disc text-stone-700 dark:text-stone-300">
              {data.assumptions.map((note) => (
                <li key={note.code}>{note.label}</li>
              ))}
            </ul>
          </details>
        </>
      )}
    </Card>
  );
}
