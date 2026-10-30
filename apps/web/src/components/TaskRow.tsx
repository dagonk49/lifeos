"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { TaskDependencies } from "@/components/TaskDependencies";
import { Badge, Button, CheckboxTarget } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { sourceHref, taskSourceLabel } from "@/lib/dashboard";
import { formatDateTime, PRIORITY_LABELS } from "@/lib/format";
import { useSpace } from "@/lib/space";
import { blockedHint } from "@/lib/tasks";
import type { Task } from "@/lib/types";

export function TaskRow({
  task,
  onDelete,
  candidates,
}: {
  task: Task;
  onDelete?: () => void;
  /** Open tasks it could wait for: shows the dependency panel (tasks page). */
  candidates?: Task[];
}) {
  const queryClient = useQueryClient();
  const { spaceName, selected } = useSpace();
  const done = task.status === "done";
  // A blocked task cannot be finished: the API would refuse it (409 task_blocked).
  const blocked = task.blocked && !done;
  const hintId = `task-${task.id}-blocked`;
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
  // Where it was created (journal, assistant): a link back to it.
  const origin = taskSourceLabel(task.source_type);
  const originHref = origin ? sourceHref(task.source_type, task.source_id) : null;

  return (
    <li className="flex items-start gap-3 py-2">
      <CheckboxTarget>
        <input
          type="checkbox"
          className="size-4 accent-teal-700 disabled:cursor-not-allowed"
          checked={done}
          disabled={blocked || toggle.isPending}
          onChange={() => toggle.mutate()}
          aria-label={done ? `Rouvrir « ${task.title} »` : `Terminer « ${task.title} »`}
          aria-describedby={blocked ? hintId : undefined}
        />
      </CheckboxTarget>
      <div className="min-w-0 flex-1">
        <p className={done ? "text-muted line-through" : ""}>{task.title}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
          {blocked && <Badge tone="amber">Bloquée</Badge>}
          {/* Overdue in words, not only in red (WCAG 1.4.1). */}
          {overdue && <Badge tone="red">En retard</Badge>}
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
          {task.tags.map((tag) => (
            <Badge key={tag} tone="blue">
              {tag}
            </Badge>
          ))}
          {origin &&
            (originHref ? (
              <Link href={originHref} aria-label={`Source : ${origin}`} className="no-underline">
                <Badge tone="violet">{origin}</Badge>
              </Link>
            ) : (
              <Badge tone="violet">{origin}</Badge>
            ))}
          {!selected && <Badge>{spaceName(task.space_id)}</Badge>}
        </div>
        {blocked && (
          <p id={hintId} className="mt-1 text-xs text-amber-800 dark:text-amber-300">
            {blockedHint(task.blocked_by)}
          </p>
        )}
        {task.notes && <p className="mt-1 whitespace-pre-wrap text-sm text-stone-600 dark:text-stone-400">{task.notes}</p>}
        {toggle.error && (
          <p role="alert" className="mt-1 text-xs text-red-700 dark:text-red-400">
            {errorMessage(toggle.error)}
          </p>
        )}
        {candidates && !done && <TaskDependencies task={task} candidates={candidates} />}
      </div>
      {onDelete && (
        <Button variant="ghost" size="sm" onClick={onDelete} aria-label={`Supprimer « ${task.title} »`}>
          Supprimer
        </Button>
      )}
    </li>
  );
}
