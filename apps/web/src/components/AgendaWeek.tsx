"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Alert, Badge, Empty, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { dayLabel, groupByDay, rangeQuery, responseLabel, timeLabel } from "@/lib/calendar";
import { formatDateTime } from "@/lib/format";
import type { CalendarEvent, CalendarEvents } from "@/lib/types";

export const EVENTS_KEY = "calendar-events";

function EventItem({ event, day }: { event: CalendarEvent; day: string }) {
  const answer = responseLabel(event);
  return (
    <li className="flex flex-col gap-0.5 rounded-md border border-stone-200 px-3 py-2 dark:border-stone-800">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs font-medium tabular-nums text-stone-600 dark:text-stone-400">
          {timeLabel(event, day)}
        </span>
        {/* Third-party text: shown as data, never interpreted. */}
        <span className="font-medium">{event.summary || "(sans titre)"}</span>
        {event.status === "tentative" && <Badge tone="amber">Provisoire</Badge>}
        {answer && <Badge tone="amber">{answer}</Badge>}
        {!event.busy && <Badge>Disponible</Badge>}
        {event.recurring && <span className="text-xs text-stone-500">· récurrent</span>}
      </div>
      {event.location && <span className="text-xs text-stone-600 dark:text-stone-400">{event.location}</span>}
      <span className="text-xs text-stone-500">
        Source : {event.source_name}
        {event.synced_at ? ` · lu le ${formatDateTime(event.synced_at)}` : ""}
      </span>
    </li>
  );
}

/**
 * Occurrences of `days` (consecutive "YYYY-MM-DD" in the owner's zone), from the followed
 * calendars of `spaceId` (every space of the principal when null).
 */
export function AgendaDays({
  days,
  spaceId,
  emptyText = "Aucun événement.",
  hideEmptyDays = false,
}: {
  days: string[];
  spaceId: string | null;
  emptyText?: string;
  hideEmptyDays?: boolean;
}) {
  const path = rangeQuery(days[0], days.length, spaceId);
  const events = useQuery({
    queryKey: [EVENTS_KEY, days[0], days.length, spaceId],
    queryFn: () => api<CalendarEvents>(path),
  });
  if (events.isPending) return <Spinner label="Chargement de l'agenda…" />;
  if (events.error) return <Alert tone="error">{errorMessage(events.error)}</Alert>;
  const groups = groupByDay(events.data.items, days);
  const shown = days.filter((d) => !hideEmptyDays || (groups.get(d)?.length ?? 0) > 0);
  if (events.data.items.length === 0) {
    return (
      <Empty>
        {emptyText}{" "}
        <Link className="text-teal-700 underline dark:text-teal-400" href="/settings#connexions">
          Choisir les agendas suivis
        </Link>
      </Empty>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {shown.map((day) => {
        const list = groups.get(day) ?? [];
        return (
          <section key={day} aria-label={dayLabel(day)}>
            <h3 className="mb-1 text-sm font-medium first-letter:uppercase">{dayLabel(day)}</h3>
            {list.length === 0 ? (
              <p className="text-xs text-stone-500">Rien de prévu.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {list.map((event) => (
                  <EventItem key={`${event.id}-${day}`} event={event} day={day} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
