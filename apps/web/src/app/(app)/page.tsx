"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { TaskRow } from "@/components/TaskRow";
import { UsageMeter } from "@/components/UsageMeter";
import { Alert, Badge, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatMoney, monthLabel, toCents } from "@/lib/finance";
import { formatDate, formatTime } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { BudgetSnapshot, Dashboard } from "@/lib/types";
import { formatRatio, formatUsd, tierQuotas } from "@/lib/usage";

function BudgetSummaryCard({ budget }: { budget: BudgetSnapshot }) {
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
      <Link className="text-teal-700 underline dark:text-teal-400" href="/finances">
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
        <Link className="text-teal-700 underline dark:text-teal-400" href="/settings#assistant-ia">
          Réglages de l&apos;assistant
        </Link>
      </p>
    </div>
  );
}

export default function DashboardPage() {
  const { selected } = useSpace();
  const dashboard = useQuery({
    queryKey: ["dashboard", selected],
    queryFn: () => api<Dashboard>(`/v1/dashboard${spaceParam(selected)}`),
    refetchInterval: 120_000,
  });

  if (dashboard.isPending) return <Spinner />;
  if (dashboard.error) return <Alert tone="error">{errorMessage(dashboard.error)}</Alert>;
  const d = dashboard.data;
  const asOf = `À jour à ${formatTime(d.generated_at)} · calculé localement`;

  return (
    <>
      <PageHeader title={`Aujourd'hui, ${formatDate(d.today)}`} />
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Priorités du jour" footer={asOf}>
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
            <Link className="text-teal-700 underline dark:text-teal-400" href="/tasks">
              Toutes les tâches
            </Link>{" "}
            · {d.tasks_without_due} sans échéance
          </p>
        </Card>

        <Card title="Cette semaine" footer={asOf}>
          {d.tasks_upcoming.length === 0 ? (
            <Empty>Aucune échéance dans les 7 prochains jours.</Empty>
          ) : (
            <ul className="divide-y divide-stone-100 dark:divide-stone-800">
              {d.tasks_upcoming.map((t) => <TaskRow key={t.id} task={t} />)}
            </ul>
          )}
        </Card>

        <Card title="Journal" footer={asOf}>
          {d.journal_today.length > 0 ? (
            <p className="text-sm">
              {d.journal_today.length} entrée(s) aujourd&apos;hui.{" "}
              <Link className="text-teal-700 underline dark:text-teal-400" href="/journal">
                Ouvrir le journal
              </Link>
            </p>
          ) : (
            <p className="text-sm">
              Pas encore d&apos;entrée aujourd&apos;hui.{" "}
              <Link className="text-teal-700 underline dark:text-teal-400" href="/journal">
                Écrire ma journée
              </Link>
            </p>
          )}
        </Card>

        <Card title="À valider" footer={asOf}>
          <ul className="flex flex-col gap-2 text-sm">
            <li>
              <Link className="text-teal-700 underline dark:text-teal-400" href="/approvals">
                {d.pending_approvals} approbation(s) en attente
              </Link>
            </li>
            <li>{d.unread_notifications} notification(s) non lue(s)</li>
          </ul>
        </Card>

        <Card title="Mémoire" footer={asOf}>
          <p className="text-sm">
            {d.memory.facts} fait(s) actif(s) · {d.memory.memories} souvenir(s) dont{" "}
            {d.memory.protected} protégé(s).{" "}
            <Link className="text-teal-700 underline dark:text-teal-400" href="/memory">
              Consulter
            </Link>
          </p>
        </Card>

        {d.budget && (
          <Card title={`Budget de ${monthLabel(d.budget.month)}`} footer={asOf}>
            <BudgetSummaryCard budget={d.budget} />
          </Card>
        )}

        <Card
          title="Assistant IA"
          actions={d.assistant.paused && <Badge tone="amber">En pause</Badge>}
          footer={asOf}
        >
          <AssistantSummary assistant={d.assistant} />
        </Card>
      </div>
    </>
  );
}
