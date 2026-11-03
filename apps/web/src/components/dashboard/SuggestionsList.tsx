import Link from "next/link";

import { Empty } from "@/components/ui";
import { formatDate } from "@/lib/format";
import type { DashboardSuggestion } from "@/lib/types";

/** Stored suggestions with why and where they come from. Review texts were written by the
 * assistant: shown as plain text (T14). */
export function SuggestionsList({ items }: { items: readonly DashboardSuggestion[] }) {
  if (items.length === 0) {
    return (
      <Empty>
        Aucune suggestion pour l&apos;instant.{" "}
        <Link className="text-teal-700 underline dark:text-teal-400" href="/reviews">
          Faire un bilan
        </Link>
      </Empty>
    );
  }
  return (
    <ul className="flex flex-col gap-2 text-sm">
      {items.map((item, index) => (
        <li key={`${item.source_type}-${item.source_id}-${index}`} className="flex flex-col gap-0.5">
          <span className="font-medium">{item.text}</span>
          {item.why && <span className="text-stone-700 dark:text-stone-300">Pourquoi : {item.why}</span>}
          <span className="text-xs text-muted">
            Source :{" "}
            <Link className="text-teal-700 underline dark:text-teal-400" href={item.href}>
              {item.source_label}
            </Link>{" "}
            · {formatDate(item.date)}
          </span>
        </li>
      ))}
    </ul>
  );
}
