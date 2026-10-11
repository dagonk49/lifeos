"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { TaskRow } from "@/components/TaskRow";
import { Alert, Badge, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatTime } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { Dashboard } from "@/lib/types";

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
  const budgetPct = d.assistant.month_token_budget
    ? Math.min(100, Math.round((100 * d.assistant.month_tokens_used) / d.assistant.month_token_budget))
    : 0;

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

        <Card title="Assistant IA" footer={asOf}>
          <div className="flex flex-col gap-2 text-sm">
            <p>
              Fournisseur : <Badge tone={d.assistant.enabled ? "teal" : "neutral"}>{d.assistant.provider}</Badge>{" "}
              {d.assistant.cloud_enabled_spaces.length === 0 && (
                <span className="text-stone-500">· aucun espace autorisé</span>
              )}
            </p>
            <div>
              <div className="flex justify-between text-xs text-stone-500">
                <span>Budget du mois</span>
                <span>
                  {d.assistant.month_tokens_used.toLocaleString("fr-FR")} /{" "}
                  {d.assistant.month_token_budget.toLocaleString("fr-FR")} jetons
                </span>
              </div>
              <div
                className="mt-1 h-2 rounded bg-stone-200 dark:bg-stone-800"
                role="progressbar"
                aria-valuenow={budgetPct}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Budget IA utilisé"
              >
                <div className="h-2 rounded bg-teal-600" style={{ width: `${budgetPct}%` }} />
              </div>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}
