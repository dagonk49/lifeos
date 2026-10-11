"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Empty, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { goalKeys } from "@/lib/goals";
import type { GoalDetail, Milestone } from "@/lib/types";

export function GoalMilestones({ goal }: { goal: GoalDetail }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const base = `/v1/goals/${goal.id}/milestones`;
  const refresh = () => queryClient.invalidateQueries({ queryKey: goalKeys.all });

  const toggle = useMutation({
    mutationFn: (milestone: Milestone) =>
      api<Milestone>(`${base}/${milestone.id}`, { method: "PATCH", body: { done: milestone.done_at === null } }),
    onSuccess: (updated) =>
      queryClient.setQueryData<GoalDetail>(goalKeys.detail(goal.id), (old) =>
        old && { ...old, milestones: old.milestones.map((m) => (m.id === updated.id ? updated : m)) },
      ),
    onSettled: refresh,
  });
  const add = useMutation({
    mutationFn: () => api<Milestone>(base, { method: "POST", body: { title: title.trim(), due_date: due || null } }),
    onSuccess: () => {
      setTitle("");
      setDue("");
      void refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (milestone: Milestone) => api(`${base}/${milestone.id}`, { method: "DELETE" }),
    onSettled: refresh,
  });
  const error = toggle.error ?? add.error ?? remove.error;

  return (
    <section aria-labelledby="goal-milestones">
      <h3 id="goal-milestones" className="text-sm font-semibold">
        Jalons
      </h3>
      {goal.milestones.length === 0 ? (
        <Empty>Aucun jalon. Découpez l&apos;objectif en étapes concrètes.</Empty>
      ) : (
        <ul className="mt-1 divide-y divide-stone-100 dark:divide-stone-800">
          {goal.milestones.map((m) => {
            const done = m.done_at !== null;
            return (
              <li key={m.id} className="flex items-start gap-3 py-2">
                <input
                  type="checkbox"
                  className="mt-1 size-4 shrink-0 accent-teal-700"
                  checked={done}
                  disabled={toggle.isPending && toggle.variables?.id === m.id}
                  onChange={() => toggle.mutate(m)}
                  aria-label={done ? `Marquer « ${m.title} » comme non atteint` : `Marquer « ${m.title} » comme atteint`}
                />
                <div className="min-w-0 flex-1 text-sm">
                  <p className={done ? "text-stone-500 line-through" : undefined}>{m.title}</p>
                  {(m.due_date || m.done_at) && (
                    <p className="text-xs text-stone-500">
                      {m.due_date && `Prévu le ${formatDate(m.due_date)}`}
                      {m.due_date && m.done_at && " · "}
                      {m.done_at && `atteint le ${formatDate(m.done_at)}`}
                    </p>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove.mutate(m)}
                  disabled={remove.isPending && remove.variables?.id === m.id}
                  aria-label={`Supprimer le jalon « ${m.title} »`}
                >
                  Supprimer
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <form
        className="mt-2 flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) add.mutate();
        }}
      >
        <Input
          aria-label="Nouveau jalon"
          placeholder="Nouveau jalon"
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
        />
        <Input
          type="date"
          aria-label="Date prévue du jalon (facultatif)"
          value={due}
          onChange={(e) => setDue(e.target.value)}
          className="sm:w-44"
        />
        <Button type="submit" variant="secondary" disabled={add.isPending || !title.trim()}>
          Ajouter
        </Button>
      </form>
      {error && (
        <div className="mt-2">
          <Alert tone="error">{errorMessage(error)}</Alert>
        </div>
      )}
    </section>
  );
}
