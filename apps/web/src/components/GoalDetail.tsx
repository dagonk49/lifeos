"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { GoalDue, GoalProgressBar, GoalStatusBadge } from "@/components/GoalCard";
import { GoalCheckins } from "@/components/GoalCheckins";
import { GoalEditForm } from "@/components/GoalEditForm";
import { GoalMilestones } from "@/components/GoalMilestones";
import { GoalTasks } from "@/components/GoalTasks";
import { Alert, Badge, Button, Spinner } from "@/components/ui";
import { api, errorMessage, isApiError } from "@/lib/api";
import { financeKeys } from "@/lib/finance";
import { formatDate, formatDateTime, todayLocal } from "@/lib/format";
import {
  CADENCE_LABELS,
  formatGoalValue,
  GOAL_KIND_LABELS,
  goalKeys,
  statusActions,
  suggestAchieved,
  type GoalFields,
} from "@/lib/goals";
import { useSpace } from "@/lib/space";
import type { GoalDetail, GoalStatus } from "@/lib/types";

const PANEL =
  "rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900";

function Section({ children }: { children: ReactNode }) {
  return <div className="mt-4 border-t border-stone-100 pt-4 dark:border-stone-800">{children}</div>;
}

function TextBlock({ title, text }: { title: string; text: string }) {
  return (
    <div className="mt-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-0.5 text-sm whitespace-pre-wrap text-stone-700 dark:text-stone-300">{text}</p>
    </div>
  );
}

type PatchBody = Partial<GoalFields> | { status: GoalStatus };

/** Detail of one goal: progress, milestones, check-ins, linked tasks and actions. */
export function GoalDetailPanel({ goalId, onClose }: { goalId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { spaceName } = useSpace();
  const [editing, setEditing] = useState(false);
  const today = todayLocal();

  const detail = useQuery({
    queryKey: goalKeys.detail(goalId),
    queryFn: () => api<GoalDetail>(`/v1/goals/${encodeURIComponent(goalId)}`),
  });

  const patch = useMutation({
    mutationFn: ({ body, version }: { body: PatchBody; version: number }) =>
      api<GoalDetail>(`/v1/goals/${goalId}`, {
        method: "PATCH",
        body,
        headers: { "If-Match": String(version) },
      }),
    onSuccess: (goal) => {
      queryClient.setQueryData(goalKeys.detail(goalId), goal);
      setEditing(false);
    },
    onError: (error) => {
      // Someone changed the goal elsewhere: reload it so the next attempt uses the new version.
      if (isApiError(error, "version_conflict")) {
        void queryClient.invalidateQueries({ queryKey: goalKeys.detail(goalId) });
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: financeKeys.projections });
      return queryClient.invalidateQueries({ queryKey: goalKeys.lists });
    },
  });

  const remove = useMutation({
    mutationFn: () => api(`/v1/goals/${goalId}`, { method: "DELETE" }),
    onSuccess: () => {
      // Lists only: refetching the deleted goal itself would fail.
      void queryClient.invalidateQueries({ queryKey: goalKeys.lists });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    },
  });

  const closeButton = (
    <Button variant="ghost" size="sm" onClick={onClose} aria-label="Fermer le détail de l'objectif">
      Fermer
    </Button>
  );

  if (detail.isPending) {
    return (
      <div className={PANEL}>
        <Spinner />
      </div>
    );
  }
  if (detail.error) {
    return (
      <div className={`${PANEL} flex flex-col items-start gap-2`}>
        <Alert tone="error">
          {isApiError(detail.error, "goal_not_found") || isApiError(detail.error, "validation_error")
            ? "Cet objectif est introuvable ou a été supprimé."
            : errorMessage(detail.error)}
        </Alert>
        {closeButton}
      </div>
    );
  }

  const goal = detail.data;
  const measurable = goal.kind === "measurable";
  const setStatus = (status: GoalStatus) => patch.mutate({ body: { status }, version: goal.version });
  const facts: [string, ReactNode][] = [];
  if (measurable) {
    if (goal.start_value) facts.push(["Départ", formatGoalValue(goal, goal.start_value)]);
    facts.push(["Valeur actuelle", formatGoalValue(goal, goal.current_value) || "Pas encore mesurée"]);
    facts.push(["Cible", formatGoalValue(goal, goal.target_value)]);
  }
  if (goal.start_date) facts.push(["Début", formatDate(goal.start_date)]);
  if (goal.due_date) facts.push(["Échéance", <GoalDue key="due" goal={goal} today={today} />]);
  facts.push(["Suivi", CADENCE_LABELS[goal.review_cadence]]);
  if (goal.achieved_at) facts.push(["Atteint le", formatDate(goal.achieved_at)]);
  facts.push(["Créé le", formatDateTime(goal.created_at)]);

  return (
    <section aria-labelledby="goal-detail-title" className={PANEL}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="goal-detail-title" className="text-lg font-semibold break-words">
            {goal.title}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <GoalStatusBadge status={goal.status} />
            <Badge>{GOAL_KIND_LABELS[goal.kind]}</Badge>
            <Badge>{spaceName(goal.space_id)}</Badge>
          </div>
        </div>
        {closeButton}
      </div>

      {editing ? (
        <div className="mt-4">
          <GoalEditForm
            goal={goal}
            saving={patch.isPending}
            error={patch.error ? errorMessage(patch.error) : null}
            onSave={(body: Partial<GoalFields>) => patch.mutate({ body, version: goal.version })}
            onCancel={() => {
              patch.reset();
              setEditing(false);
            }}
          />
        </div>
      ) : (
        <>
          <div className="mt-4">
            <GoalProgressBar goal={goal} />
          </div>
          {suggestAchieved(goal) && (
            <div className="mt-3">
              <Alert tone="success">
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    {goal.progress.basis === "metric" ? "La valeur cible est atteinte" : "Tous les jalons sont atteints"}{" "}
                    : bravo !
                  </span>
                  <Button size="sm" onClick={() => setStatus("achieved")} disabled={patch.isPending}>
                    Marquer comme atteint
                  </Button>
                </span>
              </Alert>
            </div>
          )}
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            {facts.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted">{label}</dt>
                <dd className="min-w-0">{value}</dd>
              </div>
            ))}
          </dl>
          {goal.motivation && <TextBlock title="Pourquoi c'est important" text={goal.motivation} />}
          {goal.description && <TextBlock title="Description" text={goal.description} />}

          <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Actions sur l'objectif">
            {statusActions(goal.status).map((action) => (
              <Button
                key={action.status}
                size="sm"
                variant={action.status === "achieved" ? "primary" : "secondary"}
                disabled={patch.isPending}
                onClick={() => setStatus(action.status)}
              >
                {action.label}
              </Button>
            ))}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                patch.reset();
                setEditing(true);
              }}
            >
              Modifier
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={remove.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    `Supprimer définitivement l'objectif « ${goal.title} » ? Ses jalons et points d'étape seront effacés ; les tâches liées sont conservées.`,
                  )
                ) {
                  remove.mutate();
                }
              }}
            >
              Supprimer
            </Button>
          </div>
          {(patch.error || remove.error) && (
            <div className="mt-2">
              <Alert tone="error">{errorMessage(patch.error ?? remove.error)}</Alert>
            </div>
          )}
        </>
      )}

      <Section>
        <GoalMilestones goal={goal} />
      </Section>
      <Section>
        <GoalCheckins goal={goal} />
      </Section>
      <Section>
        <GoalTasks goal={goal} />
      </Section>
    </section>
  );
}
