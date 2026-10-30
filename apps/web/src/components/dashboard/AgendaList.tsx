import Link from "next/link";

import { Badge, Empty } from "@/components/ui";
import { itemSource, itemTime, kindLabel } from "@/lib/dashboard";
import type { AgendaItem } from "@/lib/types";

const LINK = "text-teal-700 underline dark:text-teal-400";

/** One agenda line: when, what, and where it comes from. Event texts come from third
 * parties: shown as plain text, never interpreted. */
export function AgendaLine({ item, compact = false }: { item: AgendaItem; compact?: boolean }) {
  const source = itemSource(item);
  const muted = item.kind === "busy";
  return (
    <li
      className={`flex min-w-0 flex-col gap-0.5 rounded-md border px-2.5 py-1.5 ${
        muted
          ? "border-dashed border-stone-300 text-stone-500 dark:border-stone-700"
          : "border-stone-200 dark:border-stone-800"
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-xs font-medium tabular-nums text-stone-600 dark:text-stone-400">{itemTime(item)}</span>
        {item.href && !muted ? (
          <Link className={`${LINK} break-words font-medium`} href={item.href}>
            {item.title}
          </Link>
        ) : (
          <span className="break-words font-medium">{item.title}</span>
        )}
        {!compact && item.kind !== "event" && item.kind !== "busy" && <Badge>{kindLabel(item)}</Badge>}
        {item.tentative && <Badge tone="amber">Provisoire</Badge>}
        {item.blocked && <Badge tone="amber">Bloquée</Badge>}
      </div>
      {item.detail && <span className="text-xs text-stone-600 dark:text-stone-400">{item.detail}</span>}
      {!compact && source && <span className="text-xs text-stone-500">{source}</span>}
    </li>
  );
}

export function AgendaList({
  items,
  emptyText,
  compact = false,
}: {
  items: readonly AgendaItem[];
  emptyText: string;
  compact?: boolean;
}) {
  if (items.length === 0) return <Empty>{emptyText}</Empty>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {items.map((item, index) => (
        <AgendaLine key={`${item.kind}-${item.source_id ?? index}-${item.day}-${item.start ?? ""}`} item={item} compact={compact} />
      ))}
    </ul>
  );
}
