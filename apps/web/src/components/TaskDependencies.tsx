"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Alert, Button, Field, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { Task } from "@/lib/types";

/** What a task waits for, inline under its row: link another open task, or unlink one. */
export function TaskDependencies({ task, candidates }: { task: Task; candidates: Task[] }) {
  const queryClient = useQueryClient();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const link = useMutation({
    mutationFn: (dependsOn: string) =>
      api<Task>(`/v1/tasks/${task.id}/dependencies`, {
        method: "POST",
        idempotent: true,
        body: { depends_on_id: dependsOn },
      }),
    onSuccess: () => {
      setChoice("");
      unlink.reset();
      refresh();
    },
  });
  const unlink = useMutation({
    mutationFn: (dependsOn: string) =>
      api(`/v1/tasks/${task.id}/dependencies/${dependsOn}`, { method: "DELETE" }),
    onSuccess: () => {
      link.reset();
      refresh();
    },
  });
  const finished = task.depends_on.length - task.blocked_by.length;
  const error = link.error ?? unlink.error;

  return (
    <div className="mt-1">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2.5"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        Dépendances{task.depends_on.length > 0 ? ` (${task.depends_on.length})` : ""}
      </Button>
      {open && (
        <div id={panelId} className="mt-1 flex flex-col gap-2 rounded-md border border-stone-200 p-3 dark:border-stone-800">
          {task.blocked_by.length > 0 ? (
            <ul className="flex flex-col gap-1 text-sm">
              {task.blocked_by.map((b) => (
                <li key={b.id} className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate">Attend « {b.title} »</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={unlink.isPending}
                    onClick={() => unlink.mutate(b.id)}
                    aria-label={`Ne plus attendre « ${b.title} »`}
                  >
                    Retirer
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">N&apos;attend aucune tâche ouverte.</p>
          )}
          {finished > 0 && (
            <p className="text-xs text-muted">
              {finished === 1 ? "1 tâche attendue est terminée." : `${finished} tâches attendues sont terminées.`}
            </p>
          )}
          {candidates.length > 0 && (
            <form
              className="flex flex-col gap-2 sm:flex-row sm:items-end"
              onSubmit={(e) => {
                e.preventDefault();
                if (choice) link.mutate(choice);
              }}
            >
              <Field label="Attendre aussi" className="flex-1">
                <Select value={choice} onChange={(e) => setChoice(e.target.value)}>
                  <option value="">Choisir une tâche ouverte…</option>
                  {candidates.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button type="submit" variant="secondary" disabled={!choice || link.isPending}>
                Lier
              </Button>
            </form>
          )}
          {error && <Alert tone="error">{errorMessage(error)}</Alert>}
        </div>
      )}
    </div>
  );
}
