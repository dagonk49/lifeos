"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { AgendaList } from "@/components/dashboard/AgendaList";
import { SuggestionsList } from "@/components/dashboard/SuggestionsList";
import { WeekSummary } from "@/components/dashboard/WeekView";
import { ConflictsList } from "@/components/Planning";
import { TaskRow } from "@/components/TaskRow";
import { UsageMeter } from "@/components/UsageMeter";
import { Badge, Card, Empty } from "@/components/ui";
import { cardTitle, sourceFooter } from "@/lib/dashboard";
import { formatMoney, ofMonth, toCents } from "@/lib/finance";
import type { BudgetSnapshot, CardId, Dashboard } from "@/lib/types";
import { formatRatio, formatUsd, tierQuotas } from "@/lib/usage";

const LINK = "text-teal-700 underline dark:text-teal-400";

function BudgetSummary({ budget }: { budget: BudgetSnapshot }) {
  const hasBudget = (toCents(budget.budget) ?? 0n) > 0n;
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>
        Dépensé : <span className="font-medium tabular-nums">{formatMoney(budget.spent, budget.currency)}</span>
        {hasBudget && (
          <>
            {" "}
            sur <span className="tabular-nums">{formatMoney(budget.budget, budget.currency)}</span> budgétés
          </>
        )}
      </p>
      {budget.alerts.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {budget.alerts.map((alert) => (
            <li key={alert.category_id}>
              <Badge tone={alert.ratio >= 1 ? "red" : "amber"}>{Math.round(alert.ratio * 100)} %</Badge>{" "}
              {alert.name} : {formatMoney(alert.spent, budget.currency)} sur{" "}
              {formatMoney(alert.budget, budget.currency)}
            </li>
          ))}
        </ul>
      ) : (
        hasBudget && <p className="text-stone-600 dark:text-stone-400">Aucune catégorie proche de son budget.</p>
      )}
      <Link className={LINK} href="/finances">
        Voir les finances
      </Link>
    </div>
  );
}

function AssistantSummary({ assistant }: { assistant: Dashboard["assistant"] }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>
        Fournisseur : <Badge tone={assistant.enabled ? "teal" : "neutral"}>{assistant.provider}</Badge>{" "}
        {assistant.cloud_enabled_spaces.length === 0 && <span className="text-stone-500">· aucun espace autorisé</span>}
      </p>
      {assistant.paused && (
        <p className="text-amber-800 dark:text-amber-200">Arrêt d&apos;urgence actif : aucun appel cloud n&apos;est effectué.</p>
      )}
      {assistant.billing === "paid" ? (
        <p>
          Coût du mois : <span className="font-medium tabular-nums">{formatUsd(assistant.cost_month_usd)}</span>
        </p>
      ) : (
        tierQuotas(assistant.requests_today, assistant.daily_request_caps).map((quota) => (
          <UsageMeter
            key={quota.tier}
            label={`Requêtes du jour · ${quota.label.toLowerCase()}`}
            value={formatRatio(quota.used, quota.limit)}
            used={quota.used}
            limit={quota.limit}
          />
        ))
      )}
      <UsageMeter
        label="Budget du mois"
        value={`${formatRatio(assistant.month_tokens_used, assistant.month_token_budget)} jetons`}
        used={assistant.month_tokens_used}
        limit={assistant.month_token_budget}
        ariaLabel="Budget IA utilisé"
      />
      <p>
        <Link className={LINK} href="/settings#assistant-ia">
          Réglages de l&apos;assistant
        </Link>
      </p>
    </div>
  );
}

function Priorities({ d }: { d: Dashboard }) {
  return (
    <>
      {d.tasks_overdue.length + d.tasks_today.length === 0 ? (
        <Empty>Rien d&apos;urgent aujourd&apos;hui.</Empty>
      ) : (
        <>
          {d.tasks_overdue.length > 0 && (
            <>
              <h3 className="text-sm font-medium text-red-700 dark:text-red-400">En retard</h3>
              <ul className="divide-y divide-stone-100 dark:divide-stone-800">
                {d.tasks_overdue.map((t) => <TaskRow key={t.id} task={t} />)}
              </ul>
            </>
          )}
          {d.tasks_today.length > 0 && (
            <>
              <h3 className="mt-2 text-sm font-medium">Aujourd&apos;hui</h3>
              <ul className="divide-y divide-stone-100 dark:divide-stone-800">
                {d.tasks_today.map((t) => <TaskRow key={t.id} task={t} />)}
              </ul>
            </>
          )}
        </>
      )}
      <p className="mt-2 text-sm">
        <Link className={LINK} href="/tasks">
          Toutes les tâches
        </Link>{" "}
        · {d.tasks_without_due} sans échéance
      </p>
    </>
  );
}

function Agenda({ d, conflictSpace }: { d: Dashboard; conflictSpace: string }) {
  const calendars = d.week?.calendars ?? 0;
  return (
    <>
      <AgendaList items={d.agenda_today ?? []} emptyText="Rien de prévu aujourd'hui." />
      {calendars === 0 && (
        <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
          Aucun agenda suivi : seules vos tâches, vos jalons et vos paiements prévus apparaissent.{" "}
          <Link className={LINK} href="/settings#connexions">
            Connecter un agenda
          </Link>
        </p>
      )}
      <h3 className="mt-3 text-sm font-medium">Conflits d&apos;agenda (14 jours)</h3>
      <ConflictsList spaceId={conflictSpace} />
      <p className="mt-2 text-sm">
        <Link className={LINK} href="/agenda">
          Voir la semaine et trouver un créneau
        </Link>
      </p>
    </>
  );
}

/** Title and body of each card, or null when it has nothing to show (budget without data). */
export function renderCard(
  id: CardId,
  d: Dashboard,
  { conflictSpace, onShowWeek }: { conflictSpace: string; onShowWeek: () => void },
): { title: ReactNode; body: ReactNode; actions?: ReactNode } | null {
  switch (id) {
    case "priorities":
      return { title: cardTitle(id), body: <Priorities d={d} /> };
    case "agenda":
      return { title: cardTitle(id), body: <Agenda d={d} conflictSpace={conflictSpace} /> };
    case "week":
      if (!d.week) return null;
      return {
        title: cardTitle(id),
        body: (
          <>
            <WeekSummary week={d.week} today={d.today} />
            <p className="mt-2 text-sm">
              <button type="button" className={LINK} onClick={onShowWeek}>
                Voir le détail de la semaine
              </button>
            </p>
          </>
        ),
      };
    case "suggestions":
      return { title: cardTitle(id), body: <SuggestionsList items={d.suggestions ?? []} /> };
    case "journal":
      return {
        title: cardTitle(id),
        body:
          d.journal_today.length > 0 ? (
            <p className="text-sm">
              {d.journal_today.length} entrée(s) aujourd&apos;hui.{" "}
              <Link className={LINK} href="/journal">
                Ouvrir le journal
              </Link>
            </p>
          ) : (
            <p className="text-sm">
              Pas encore d&apos;entrée aujourd&apos;hui.{" "}
              <Link className={LINK} href="/journal">
                Écrire ma journée
              </Link>
            </p>
          ),
      };
    case "approvals":
      return {
        title: cardTitle(id),
        body: (
          <ul className="flex flex-col gap-2 text-sm">
            <li>
              <Link className={LINK} href="/approvals">
                {d.pending_approvals} approbation(s) en attente
              </Link>
            </li>
            <li>{d.unread_notifications} notification(s) non lue(s)</li>
          </ul>
        ),
      };
    case "memory":
      return {
        title: cardTitle(id),
        body: (
          <p className="text-sm">
            {d.memory.facts} fait(s) actif(s) · {d.memory.memories} souvenir(s) dont {d.memory.protected}{" "}
            protégé(s).{" "}
            <Link className={LINK} href="/memory">
              Consulter
            </Link>
          </p>
        ),
      };
    case "budget":
      if (!d.budget) return null;
      return { title: `Budget ${ofMonth(d.budget.month)}`, body: <BudgetSummary budget={d.budget} /> };
    case "assistant":
      return {
        title: cardTitle(id),
        actions: d.assistant.paused && <Badge tone="amber">En pause</Badge>,
        body: <AssistantSummary assistant={d.assistant} />,
      };
  }
}

const FALLBACK_FOOTERS: Partial<Record<CardId, string>> = {
  week: "Tâches, jalons et paiements prévus calculés localement",
};

export function DashboardCard({
  id,
  d,
  conflictSpace,
  onShowWeek,
}: {
  id: CardId;
  d: Dashboard;
  conflictSpace: string;
  onShowWeek: () => void;
}) {
  const card = renderCard(id, d, { conflictSpace, onShowWeek });
  if (!card) return null;
  const footer = sourceFooter(d.sources?.[id], FALLBACK_FOOTERS[id] ?? "Calculé localement");
  return (
    <Card title={card.title} actions={card.actions} footer={footer}>
      {card.body}
    </Card>
  );
}
