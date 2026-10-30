"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { AgendaLine } from "@/components/dashboard/AgendaList";
import { Alert, Button, Card, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { addDays, dayLabel, weekLabel } from "@/lib/calendar";
import { daySummary, sourceFooter } from "@/lib/dashboard";
import type { AgendaItem, DashboardWeek } from "@/lib/types";

export const WEEK_KEY = "dashboard-week";

function weekPath(start: string, spaceId: string | null): string {
  const params = new URLSearchParams({ start });
  if (spaceId) params.set("space_id", spaceId);
  return `/v1/dashboard/week?${params.toString()}`;
}

/** Seven days side by side on a wide screen, stacked on a phone. The current week comes
 * with the dashboard; the others are read on demand. */
export function WeekView({
  initial,
  spaceId,
  today,
}: {
  initial: DashboardWeek;
  spaceId: string | null;
  today: string;
}) {
  const [start, setStart] = useState(initial.start);
  const other = useQuery({
    queryKey: [WEEK_KEY, start, spaceId],
    queryFn: () => api<DashboardWeek>(weekPath(start, spaceId)),
    enabled: start !== initial.start,
  });
  const week = start === initial.start ? initial : other.data;
  const source = week
    ? week.calendars > 0
      ? { origin: "calendar" as const, as_of: week.synced_at, count: week.calendars }
      : undefined
    : undefined;
  return (
    <Card
      title={weekLabel(start)}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => setStart(addDays(start, -7))}>
            Semaine précédente
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setStart(initial.start)} disabled={start === initial.start}>
            Cette semaine
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setStart(addDays(start, 7))}>
            Semaine suivante
          </Button>
        </div>
      }
      footer={`${sourceFooter(source, "Aucun agenda suivi")} · tâches, jalons et paiements prévus calculés localement`}
    >
      {!week ? (
        other.error ? (
          <Alert tone="error">{errorMessage(other.error)}</Alert>
        ) : (
          <Spinner label="Chargement de la semaine…" />
        )
      ) : (
        <>
          {week.calendars === 0 && (
            <p className="mb-2 text-sm text-stone-600 dark:text-stone-400">
              Aucun agenda suivi : seules vos tâches, vos jalons et vos paiements prévus apparaissent.{" "}
              <Link className="text-teal-700 underline dark:text-teal-400" href="/settings#connexions">
                Connecter un agenda
              </Link>
            </p>
          )}
          <div className="grid gap-3 lg:grid-cols-7">
            {week.days.map((day) => (
              <section
                key={day.date}
                aria-label={dayLabel(day.date)}
                className={`min-w-0 rounded-md p-1.5 ${day.date === today ? "bg-teal-50 dark:bg-teal-950/40" : ""}`}
              >
                <h3 className="mb-1 text-sm font-medium first-letter:uppercase">
                  {dayLabel(day.date)}
                  {day.date === today && <span className="text-xs font-normal text-muted"> · aujourd&apos;hui</span>}
                </h3>
                {day.items.length === 0 ? (
                  <p className="text-xs text-muted">Rien de prévu.</p>
                ) : (
                  <ul className="flex flex-col gap-1 text-sm">
                    {day.items.map((item, index) => (
                      <AgendaLine key={`${item.kind}-${item.source_id ?? index}-${item.start ?? ""}`} item={item} compact />
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

/** Compact week of the day view: what each day holds, titles from tomorrow on (today's
 * are in the agenda and the priorities), never the content of an occurrence. */
export function WeekSummary({ week, today }: { week: DashboardWeek; today: string }) {
  return (
    <ul className="flex flex-col gap-1.5 text-sm">
      {week.days.map((day) => {
        const named = day.date > today ? day.items.filter((i: AgendaItem) => i.kind !== "event" && i.kind !== "busy") : [];
        return (
          <li key={day.date} className={day.date < today ? "text-muted" : ""}>
            <span className="font-medium first-letter:uppercase">{dayLabel(day.date)}</span>
            {day.date === today && <span className="text-xs text-muted"> · aujourd&apos;hui</span>} :{" "}
            {daySummary(day.items)}
            {named.length > 0 && (
              <ul className="ml-4 list-disc text-xs text-stone-600 dark:text-stone-400">
                {named.slice(0, 5).map((item, index) => (
                  <li key={`${item.kind}-${item.source_id ?? index}`}>
                    {item.href ? (
                      <Link className="text-teal-700 underline dark:text-teal-400" href={item.href}>
                        {item.title}
                      </Link>
                    ) : (
                      item.title
                    )}
                    {item.blocked && " (bloquée)"}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
