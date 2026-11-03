"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { BlockSlotButton } from "@/components/CalendarChange";
import { Alert, Badge, Button, Empty, Field, Input, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { dayLabel } from "@/lib/calendar";
import type { PlanningBusy, PlanningConflicts, PlanningSlots } from "@/lib/types";

/** "jeudi 22 octobre, 09:00 – 10:00" from instants written in the owner's zone. */
export function spanLabel(start: string, end: string): string {
  const day = dayLabel(start.slice(0, 10));
  const endDay = end.slice(0, 10);
  const until = endDay === start.slice(0, 10) ? end.slice(11, 16) : `${dayLabel(endDay)} ${end.slice(11, 16)}`;
  return `${day}, ${start.slice(11, 16)} – ${until}`;
}

function BusyLabel({ busy }: { busy: PlanningBusy }) {
  return (
    <span>
      {busy.label}
      {busy.tentative && " (provisoire)"}
    </span>
  );
}

/** Overlapping occurrences of the next 14 days (computed on the server, no AI). */
export function ConflictsList({ spaceId }: { spaceId: string }) {
  const conflicts = useQuery({
    queryKey: ["planning-conflicts", spaceId],
    queryFn: () => api<PlanningConflicts>(`/v1/planning/conflicts?space_id=${encodeURIComponent(spaceId)}`),
  });
  if (conflicts.isPending) return <Spinner />;
  if (conflicts.error) return <Alert tone="warning">{errorMessage(conflicts.error)}</Alert>;
  if (conflicts.data.calendars === 0) return <Empty>Aucun agenda suivi.</Empty>;
  if (conflicts.data.items.length === 0) return <Empty>Aucun conflit dans les 14 prochains jours.</Empty>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {conflicts.data.items.map((c) => (
        <li key={`${c.first.start}-${c.second.start}-${c.first.label}-${c.second.label}`}>
          <Badge tone="amber">Conflit</Badge> <BusyLabel busy={c.first} /> et <BusyLabel busy={c.second} /> ·{" "}
          {spanLabel(c.start, c.end)}
        </li>
      ))}
    </ul>
  );
}

/** Free slots for a duration, with what each proposal rests on. « Bloquer ce créneau »
 * only proposes the event: the calendar changes once the owner approves it. */
export function SlotFinder({ spaceId, taskId }: { spaceId: string; taskId?: string }) {
  const [minutes, setMinutes] = useState(taskId ? "" : "60");
  const find = useMutation({
    mutationFn: () =>
      api<PlanningSlots>("/v1/planning/slots", {
        method: "POST",
        body: {
          space_id: spaceId,
          ...(taskId ? { task_id: taskId } : {}),
          ...(minutes ? { duration_minutes: Number(minutes) } : {}),
        },
      }),
  });
  return (
    <div className="flex flex-col gap-2">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          find.mutate();
        }}
      >
        <Field label="Durée (minutes)" hint={taskId ? "Vide : estimation de la tâche, sinon 1 h." : undefined}>
          <Input
            type="number"
            min={5}
            max={720}
            step={5}
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
            className="w-28"
          />
        </Field>
        <Button type="submit" size="sm" disabled={find.isPending}>
          Proposer un créneau
        </Button>
      </form>
      {find.error && <Alert tone="error">{errorMessage(find.error)}</Alert>}
      {find.data &&
        (find.data.items.length === 0 ? (
          <Empty>Aucun créneau libre dans les 14 prochains jours (ou avant l&apos;échéance).</Empty>
        ) : (
          <ol aria-label="Créneaux proposés" className="flex flex-col gap-2 text-sm">
            {find.data.items.map((slot) => (
              <li key={slot.start} className="rounded-md border border-stone-200 p-2 dark:border-stone-800">
                <p className="font-medium">{spanLabel(slot.start, slot.end)}</p>
                <BlockSlotButton
                  spaceId={spaceId}
                  start={slot.start}
                  end={slot.end}
                  title={find.data.task_title ?? "Créneau réservé"}
                />
                <details className="mt-1 text-xs">
                  <summary>Hypothèses et événements pris en compte</summary>
                  <ul className="ml-4 list-disc">
                    {slot.assumptions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                  {slot.events_used.length > 0 && (
                    <ul aria-label="Événements autour" className="ml-4 mt-1 list-disc">
                      {slot.events_used.map((e) => (
                        <li key={`${e.start}-${e.label}`}>
                          <BusyLabel busy={e} /> · {spanLabel(e.start, e.end)}
                        </li>
                      ))}
                    </ul>
                  )}
                </details>
              </li>
            ))}
          </ol>
        ))}
    </div>
  );
}
