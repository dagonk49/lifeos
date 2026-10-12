"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Alert, Badge, Card, Empty, Spinner, cx } from "@/components/ui";
import { api } from "@/lib/api";
import {
  barWidth,
  budgetState,
  countLabel,
  financeErrorMessage,
  financeKeys,
  formatCents,
  formatMoney,
  hasBudget,
  ofMonth,
  otherCurrencyNote,
  remainingText,
  spentText,
  summaryPath,
  summaryRows,
  toCents,
  type BudgetState,
} from "@/lib/finance";
import { formatDate } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { BudgetSummary, CategorySummary } from "@/lib/types";

const BAR: Record<BudgetState, string> = {
  none: "bg-stone-400 dark:bg-stone-600",
  ok: "bg-teal-600",
  near: "bg-amber-500",
  over: "bg-red-600",
};

function Total({ label, value, note }: { label: string; value: string; note?: ReactNode }) {
  return (
    <div className="rounded-md bg-stone-50 px-3 py-2 dark:bg-stone-800/60">
      <dt className="text-xs text-stone-500">{label}</dt>
      <dd className="font-semibold tabular-nums">{value}</dd>
      {note && <dd className="text-xs text-stone-500">{note}</dd>}
    </div>
  );
}

function ExpenseRow({ row, currency }: { row: CategorySummary; currency: string }) {
  const state = budgetState(row);
  const remaining = remainingText(row.remaining, currency);
  return (
    <li>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <span className="font-medium">
          {row.name}
          {row.archived && <span className="ml-1 text-xs font-normal text-stone-500">(archivée)</span>}
        </span>
        <span className="text-sm tabular-nums">{spentText(row, currency)}</span>
      </div>
      {row.budget !== null && (
        <>
          {/* Decorative: the text above and below says the same, colour is never the only cue. */}
          <div className="mt-1 h-2 overflow-hidden rounded bg-stone-200 dark:bg-stone-800" aria-hidden>
            <div className={cx("h-2 rounded", BAR[state])} style={{ width: `${barWidth(row.ratio)}%` }} />
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
            {state === "over" && <Badge tone="red">Budget dépassé</Badge>}
            {state === "near" && <Badge tone="amber">Bientôt atteint</Badge>}
            {remaining && (
              <span className={state === "over" ? "font-medium text-red-700 dark:text-red-400" : "text-stone-500"}>
                {remaining}
              </span>
            )}
          </div>
        </>
      )}
    </li>
  );
}

function SummaryBody({ summary }: { summary: BudgetSummary }) {
  const { currency, totals, uncategorised } = summary;
  const rows = summaryRows(summary);
  const budgeted = hasBudget(summary);
  const balance = toCents(totals.balance) ?? 0n;
  const remaining = toCents(totals.budget_remaining) ?? 0n;
  const otherCurrencies = otherCurrencyNote(summary.other_currency_transactions);
  const uncategorisedParts = [
    (toCents(uncategorised.expenses) ?? 0n) > 0n && `${formatMoney(uncategorised.expenses, currency)} de dépenses`,
    (toCents(uncategorised.income) ?? 0n) > 0n && `${formatMoney(uncategorised.income, currency)} de revenus`,
  ].filter(Boolean);

  return (
    <>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Total label="Dépenses" value={formatMoney(totals.expenses, currency)} />
        <Total label="Revenus" value={formatMoney(totals.income, currency)} />
        <Total
          label="Solde"
          value={formatMoney(totals.balance, currency, "always")}
          note={balance < 0n ? "Plus de dépenses que de revenus" : undefined}
        />
        <Total
          label="Budget"
          value={budgeted ? formatMoney(totals.budget, currency) : "Aucun"}
          note={budgeted ? `${formatMoney(totals.budgeted_spent, currency)} dépensés` : "Fixez un budget par catégorie."}
        />
        {budgeted && (
          <Total
            label={remaining < 0n ? "Dépassement" : "Reste"}
            value={formatCents(remaining < 0n ? -remaining : remaining, currency)}
            note={remaining < 0n ? "Budget total dépassé" : undefined}
          />
        )}
      </dl>

      {otherCurrencies && (
        <div className="mt-3">
          <Alert tone="info">{otherCurrencies}</Alert>
        </div>
      )}

      <h3 className="mt-4 mb-2 text-sm font-semibold">Dépenses par catégorie</h3>
      {rows.expenses.length === 0 ? (
        <Empty>Aucune dépense classée ni aucun budget ce mois-ci.</Empty>
      ) : (
        <ul className="flex flex-col gap-3 text-sm">
          {rows.expenses.map((row) => (
            <ExpenseRow key={row.category_id} row={row} currency={currency} />
          ))}
        </ul>
      )}

      {rows.incomes.length > 0 && (
        <>
          <h3 className="mt-4 mb-2 text-sm font-semibold">Revenus par catégorie</h3>
          <ul className="flex flex-col gap-1 text-sm">
            {rows.incomes.map((row) => (
              <li key={row.category_id} className="flex flex-wrap justify-between gap-x-2">
                <span className="font-medium">{row.name}</span>
                <span className="tabular-nums">{formatMoney(row.received, currency)} reçus</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {uncategorised.transactions > 0 && (
        <p className="mt-4 text-sm">
          <span className="font-medium">Non classées : </span>
          {uncategorisedParts.join(" et ")} ({countLabel(uncategorised.transactions, "opération", "opérations")}).
        </p>
      )}
    </>
  );
}

/** Month budget of one space, computed locally by the server in exact decimals. */
export function FinanceSummary({ spaceId, month }: { spaceId: string; month: string }) {
  const { spaces, spaceName } = useSpace();
  const summary = useQuery({
    queryKey: financeKeys.summary(spaceId, month),
    queryFn: () => api<BudgetSummary>(summaryPath(spaceId, month)),
  });
  const title = `Budget ${ofMonth(month)}${spaces.length > 1 ? ` · ${spaceName(spaceId)}` : ""}`;

  return (
    <Card
      title={title}
      footer={
        summary.data
          ? `Calculé localement, au centime près, du ${formatDate(summary.data.period_start)} au ${formatDate(summary.data.period_end)}.`
          : undefined
      }
    >
      {summary.isPending ? (
        <Spinner />
      ) : !summary.data ? (
        <Alert tone="error">{financeErrorMessage(summary.error)}</Alert>
      ) : (
        <SummaryBody summary={summary.data} />
      )}
    </Card>
  );
}
