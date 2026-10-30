"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Badge, Button, CheckboxTarget, Empty, Field, Input, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, STATUS_LABELS } from "@/lib/format";
import { goalKeys, isOpenGoal, sortGoalTasks } from "@/lib/goals";
import { blockedHint } from "@/lib/tasks";
import type { GoalDetail, Page, Task } from "@/lib/types";

/** Tasks linked to a goal (`GET /v1/tasks?goal_id=`), with a quick way to add one. */
export function GoalTasks({ goal }: { goal: GoalDetail }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const tasks = useQuery({
    queryKey: goalKeys.tasks(goal.id),
    queryFn: () => api<Page<Task>>(`/v1/tasks?goal_id=${encodeURIComponent(goal.id)}&limit=200`),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    void queryClient.invalidateQueries({ queryKey: goalKeys.all });
  };
  const toggle = useMutation({
    mutationFn: (task: Task) =>
      api<Task>(`/v1/tasks/${task.id}`, {
        method: "PATCH",
        body: { status: task.status === "done" ? "todo" : "done" },
        headers: { "If-Match": String(task.version) },
      }),
    onSettled: refresh,
  });
  const add = useMutation({
    mutationFn: () =>
      api<Task>("/v1/tasks", {
        method: "POST",
        idempotent: true,
        body: { space_id: goal.space_id, title: title.trim(), goal_id: goal.id },
      }),
    onSuccess: () => {
      setTitle("");
      refresh();
    },
  });
  const error = toggle.error ?? add.error;
  const items = sortGoalTasks(tasks.data?.items ?? []);

  return (
    <section aria-labelledby="goal-tasks">
      <h3 id="goal-tasks" className="text-sm font-semibold">
        Tâches liées
      </h3>
      {tasks.isPending ? (
        <Spinner />
      ) : tasks.error ? (
        <Alert tone="error">{errorMessage(tasks.error)}</Alert>
      ) : items.length === 0 ? (
        <Empty>Aucune tâche liée à cet objectif.</Empty>
      ) : (
        <ul className="mt-1 divide-y divide-stone-100 dark:divide-stone-800">
          {items.map((t) => {
            const done = t.status === "done";
            const cancelled = t.status === "cancelled";
            const overdue = !done && !cancelled && t.due_at !== null && new Date(t.due_at) < new Date();
            // Waiting for an open task: the API refuses to finish it (409 task_blocked).
            const blocked = t.blocked && !done;
            const hintId = `goal-task-${t.id}-blocked`;
            return (
              <li key={t.id} className="flex items-start gap-3 py-2 text-sm">
                <CheckboxTarget>
                  <input
                    type="checkbox"
                    className="size-4 accent-teal-700 disabled:cursor-not-allowed"
                    checked={done}
                    disabled={cancelled || blocked || (toggle.isPending && toggle.variables?.id === t.id)}
                    onChange={() => toggle.mutate(t)}
                    aria-label={done ? `Rouvrir « ${t.title} »` : `Terminer « ${t.title} »`}
                    aria-describedby={blocked ? hintId : undefined}
                  />
                </CheckboxTarget>
                <div className="min-w-0 flex-1">
                  <p className={done || cancelled ? "text-muted line-through" : undefined}>{t.title}</p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    {t.status !== "todo" && <Badge tone={done ? "teal" : "neutral"}>{STATUS_LABELS[t.status]}</Badge>}
                    {/* Overdue in words, not only in red (WCAG 1.4.1). */}
                    {overdue && <Badge tone="red">En retard</Badge>}
                    {t.due_at && !done && !cancelled && (
                      <span className={overdue ? "font-medium text-red-700 dark:text-red-400" : undefined}>
                        Échéance {formatDateTime(t.due_at)}
                      </span>
                    )}
                  </div>
                  {blocked && (
                    <p id={hintId} className="mt-0.5 text-xs text-amber-800 dark:text-amber-300">
                      {blockedHint(t.blocked_by)}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {isOpenGoal(goal.status) && (
        <form
          className="mt-2 flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (title.trim()) add.mutate();
          }}
        >
          <Field label="Nouvelle tâche liée à l'objectif" className="flex-1">
            <Input value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Button
            type="submit"
            variant="secondary"
            className="sm:self-end"
            disabled={add.isPending || !title.trim()}
          >
            Ajouter
          </Button>
        </form>
      )}
      {error && (
        <div className="mt-2">
          <Alert tone="error">{errorMessage(error)}</Alert>
        </div>
      )}
    </section>
  );
}
