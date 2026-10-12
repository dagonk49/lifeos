"use client";

import { suggestionText } from "@/lib/agents";

/** Dismissible hint: an agent that could help with this kind of request (never automatic). */
export function AgentSuggestion({
  label,
  onUse,
  onDismiss,
}: {
  label: string;
  onUse: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      role="status"
      className="flex max-w-full items-start gap-1 self-start rounded-2xl border border-violet-200 bg-violet-50 py-1 pl-3 pr-1 text-sm text-violet-900 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-100"
    >
      <button
        type="button"
        onClick={onUse}
        className="min-w-0 py-0.5 text-left underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-700"
      >
        {suggestionText(label)}
      </button>
      <button
        type="button"
        aria-label="Ignorer la suggestion"
        title="Ignorer la suggestion"
        onClick={onDismiss}
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-full hover:bg-violet-200/70 focus-visible:outline-2 focus-visible:outline-violet-700 dark:hover:bg-violet-900"
      >
        <span aria-hidden>×</span>
      </button>
    </div>
  );
}
