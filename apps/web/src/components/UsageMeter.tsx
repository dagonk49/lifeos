import type { ReactNode } from "react";

import { cx } from "@/components/ui";
import { gauge, type UsageLevel } from "@/lib/usage";

const FILL: Record<UsageLevel, string> = {
  ok: "bg-teal-600 dark:bg-teal-500",
  warning: "bg-amber-500 dark:bg-amber-400",
  exceeded: "bg-red-600 dark:bg-red-500",
};

/**
 * Usage gauge: a label, the "used / limit" text and a progress bar coloured by level
 * (amber from 80 %, red once the limit is reached).
 */
export function UsageMeter({
  label,
  value,
  used,
  limit,
  ariaLabel,
  hint,
}: {
  label: ReactNode;
  /** Visible "used / limit" text, also read as the bar's value. */
  value: string;
  used: number;
  limit: number;
  /** Accessible name of the bar (defaults to the label when it is plain text). */
  ariaLabel?: string;
  hint?: ReactNode;
}) {
  const { percent, level } = gauge(used, limit);
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-xs text-stone-600 dark:text-stone-400">
        <span>{label}</span>
        <span className={cx("tabular-nums", level === "exceeded" && "font-medium text-red-700 dark:text-red-400")}>
          {value}
        </span>
      </div>
      <div
        className="mt-1 h-2 overflow-hidden rounded bg-stone-200 dark:bg-stone-800"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={`${value} (${percent} %)`}
        aria-label={ariaLabel ?? (typeof label === "string" ? label : undefined)}
      >
        <div className={cx("h-2 rounded", FILL[level])} style={{ width: `${percent}%` }} />
      </div>
      {hint && <p className="mt-0.5 text-xs text-stone-500">{hint}</p>}
    </div>
  );
}
