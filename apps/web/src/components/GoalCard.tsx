"use client";

import Link from "next/link";

import { Badge, cx } from "@/components/ui";
import { formatDate } from "@/lib/format";
import {
  dueHint,
  formatPercent,
  GOAL_KIND_LABELS,
  GOAL_STATUS_LABELS,
  GOAL_STATUS_TONES,
  isOpenGoal,
  progressLabel,
  progressPercent,
  tasksLabel,
} from "@/lib/goals";
import type { GoalStatus, GoalSummary } from "@/lib/types";

export function GoalStatusBadge({ status }: { status: GoalStatus }) {
  return <Badge tone={GOAL_STATUS_TONES[status]}>{GOAL_STATUS_LABELS[status]}</Badge>;
}

/**
 * Progress bar with its French label. `decorative` hides the bar from assistive
 * technologies when the label is already read (e.g. inside a link).
 */
export function GoalProgressBar({ goal, decorative = false }: { goal: GoalSummary; decorative?: boolean }) {
  const percent = progressPercent(goal.progress.ratio);
  const label = progressLabel(goal);
  const muted = !isOpenGoal(goal.status) && goal.status !== "achieved";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-xs text-stone-600 dark:text-stone-400">
        <span>{label}</span>
        {percent !== null && <span className="font-medium tabular-nums">{formatPercent(goal.progress.ratio)}</span>}
      </div>
      <div
        className="mt-1 h-2 overflow-hidden rounded bg-stone-200 dark:bg-stone-800"
        {...(decorative
          ? { "aria-hidden": true }
          : {
              role: "progressbar",
              "aria-label": `Progression de « ${goal.title} »`,
              "aria-valuemin": 0,
              "aria-valuemax": 100,
              "aria-valuenow": percent ?? undefined,
              "aria-valuetext": percent === null ? label : `${formatPercent(goal.progress.ratio)} (${label})`,
            })}
      >
        <div
          className={cx("h-2 rounded", muted ? "bg-stone-400 dark:bg-stone-600" : "bg-teal-600")}
          style={{ width: `${percent ?? 0}%` }}
        />
      </div>
    </div>
  );
}

export function GoalDue({ goal, today }: { goal: Pick<GoalSummary, "due_date" | "status">; today: string }) {
  if (!goal.due_date) return null;
  const hint = isOpenGoal(goal.status) ? dueHint(goal.due_date, today) : null;
  return (
    <span className={hint?.overdue ? "font-medium text-red-700 dark:text-red-400" : undefined}>
      Échéance {formatDate(goal.due_date)}
      {hint && ` (${hint.text})`}
    </span>
  );
}

export function GoalCard({
  goal,
  current,
  spaceLabel,
  today,
}: {
  goal: GoalSummary;
  current: boolean;
  /** Space name, shown when all spaces are listed. */
  spaceLabel?: string;
  today: string;
}) {
  return (
    <li>
      <Link
        href={`/goals?id=${goal.id}`}
        aria-current={current ? "true" : undefined}
        className={cx(
          "block rounded-lg border bg-white p-4 shadow-sm transition-colors focus-visible:outline-2 focus-visible:outline-teal-600 dark:bg-stone-900",
          current
            ? "border-teal-600 ring-1 ring-teal-600 dark:border-teal-500 dark:ring-teal-500"
            : "border-stone-200 hover:border-stone-400 dark:border-stone-800 dark:hover:border-stone-600",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <h3 className="min-w-0 font-semibold break-words">{goal.title}</h3>
          <GoalStatusBadge status={goal.status} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-stone-500">
          <Badge>{GOAL_KIND_LABELS[goal.kind]}</Badge>
          {spaceLabel && <Badge>{spaceLabel}</Badge>}
          <GoalDue goal={goal} today={today} />
        </div>
        <div className="mt-3">
          <GoalProgressBar goal={goal} decorative />
        </div>
        <p className="mt-2 text-xs text-stone-500">{tasksLabel(goal.progress.tasks_open, goal.progress.tasks_done)}</p>
      </Link>
    </li>
  );
}
