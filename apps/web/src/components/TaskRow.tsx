"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge, Button } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime, PRIORITY_LABELS } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { Task } from "@/lib/types";

export function TaskRow({ task, onDelete }: { task: Task; onDelete?: () => void }) {
  const queryClient = useQueryClient();
  const { spaceName, selected } = useSpace();
  const done = task.status === "done";
  const toggle = useMutation({
    mutationFn: () =>
      api<Task>(`/v1/tasks/${task.id}`, {
        method: "PATCH",
        body: { status: done ? "todo" : "done" },
        headers: { "If-Match": String(task.version) },
      }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      // A linked goal's progress counts open and done tasks.
      if (task.goal_id) void queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
  });
  const overdue = !done && task.due_at !== null && new Date(task.due_at) < new Date();

  return (
    <li className="flex items-start gap-3 py-2">
      <input
        type="checkbox"
        className="mt-1 size-4 accent-teal-700"
        checked={done}
        onChange={() => toggle.mutate()}
        aria-label={done ? `Rouvrir « ${task.title} »` : `Terminer « ${task.title} »`}
      />
      <div className="min-w-0 flex-1">
        <p className={done ? "text-stone-500 line-through" : ""}>{task.title}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-stone-500">
          {task.due_at && (
            <span className={overdue ? "font-medium text-red-700 dark:text-red-400" : ""}>
              Échéance {formatDateTime(task.due_at)}
            </span>
          )}
          {task.remind_at && <span>· Rappel {formatDateTime(task.remind_at)}</span>}
          {task.priority !== "normal" && (
            <Badge tone={task.priority === "high" ? "amber" : "neutral"}>
              {PRIORITY_LABELS[task.priority]}
            </Badge>
          )}
          {task.source_type === "journal_entry" && <Badge tone="violet">Journal</Badge>}
          {!selected && <Badge>{spaceName(task.space_id)}</Badge>}
        </div>
        {task.notes && <p className="mt-1 whitespace-pre-wrap text-sm text-stone-600">{task.notes}</p>}
      </div>
      {onDelete && (
        <Button variant="ghost" size="sm" onClick={onDelete} aria-label={`Supprimer « ${task.title} »`}>
          Supprimer
        </Button>
      )}
    </li>
  );
}
