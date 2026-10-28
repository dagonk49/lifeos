"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApprovalReview } from "@/components/ApprovalReview";
import { Alert, Button, Field, Input, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { addDays } from "@/lib/calendar";
import type { Approval, CalendarEvent, WritableCalendar } from "@/lib/types";

/**
 * Calendar changes (task 16). Every button here only PROPOSES: the server reads the event
 * again at the provider and returns an approval with the exact preview, shown inline. The
 * calendar changes only once the owner approves it (with a passkey for a shared event).
 * Times are sent without offset: the server reads them in the owner's zone.
 */

/** "2026-10-27T14:00" from two inputs ("2026-10-27", "14:00"). */
export function localMoment(day: string, time: string): string {
  return `${day}T${time}`;
}

function Proposal({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  return (
    <div className="mt-2" aria-label="Aperçu à approuver">
      <ApprovalReview approval={approval} onDone={onDone} />
    </div>
  );
}

/** « Bloquer ce créneau » on a slot proposed by planning: a calendar.create_event approval. */
export function BlockSlotButton({
  spaceId,
  start,
  end,
  title,
}: {
  spaceId: string;
  start: string;
  end: string;
  title: string;
}) {
  const [approval, setApproval] = useState<Approval | null>(null);
  const propose = useMutation({
    mutationFn: () =>
      api<Approval>("/v1/calendar/events", {
        method: "POST",
        idempotent: true,
        body: { space_id: spaceId, summary: title, starts_at: start, ends_at: end },
      }),
    onSuccess: setApproval,
  });
  if (approval) return <Proposal approval={approval} onDone={() => setApproval(null)} />;
  return (
    <div className="mt-2 flex flex-col gap-1">
      <div>
        <Button size="sm" variant="secondary" onClick={() => propose.mutate()} disabled={propose.isPending}>
          Bloquer ce créneau
        </Button>
      </div>
      {propose.error && <Alert tone="error">{errorMessage(propose.error)}</Alert>}
    </div>
  );
}

/** « Déplacer » and « Annuler » under an occurrence of a calendar that may be modified. */
export function EventActions({ event }: { event: CalendarEvent }) {
  const [mode, setMode] = useState<"idle" | "move">("idle");
  const [approval, setApproval] = useState<Approval | null>(null);
  const startDay = event.all_day ? (event.start_date ?? "") : (event.starts_at ?? "").slice(0, 10);
  const [day, setDay] = useState(startDay);
  const [from, setFrom] = useState(event.all_day ? "09:00" : (event.starts_at ?? "").slice(11, 16));
  const [to, setTo] = useState(event.all_day ? "10:00" : (event.ends_at ?? "").slice(11, 16));
  const [notify, setNotify] = useState(false);
  const done = (result: Approval) => {
    setApproval(result);
    setMode("idle");
  };
  const move = useMutation({
    mutationFn: () => {
      const days = event.all_day && event.start_date && event.end_date ? spanDays(event.start_date, event.end_date) : 0;
      const body = event.all_day
        ? { start_date: day, end_date: addDays(day, days), notify }
        : { starts_at: localMoment(day, from), ends_at: localMoment(day, to), notify };
      return api<Approval>(`/v1/calendar/events/${event.id}/move`, { method: "POST", idempotent: true, body });
    },
    onSuccess: done,
  });
  const cancel = useMutation({
    mutationFn: () =>
      api<Approval>(`/v1/calendar/events/${event.id}/cancel`, {
        method: "POST",
        idempotent: true,
        body: { notify },
      }),
    onSuccess: done,
  });
  if (approval) return <Proposal approval={approval} onDone={() => setApproval(null)} />;
  const error = move.error ?? cancel.error;
  const busy = move.isPending || cancel.isPending;
  return (
    <div className="mt-1 flex flex-col gap-2">
      {mode === "move" ? (
        <form
          aria-label={`Déplacer ${event.summary || "l'événement"}`}
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            move.mutate();
          }}
        >
          <Field label="Jour">
            <Input type="date" required value={day} onChange={(e) => setDay(e.target.value)} className="w-40" />
          </Field>
          {!event.all_day && (
            <>
              <Field label="Début">
                <Input type="time" required value={from} onChange={(e) => setFrom(e.target.value)} className="w-28" />
              </Field>
              <Field label="Fin">
                <Input type="time" required value={to} onChange={(e) => setTo(e.target.value)} className="w-28" />
              </Field>
            </>
          )}
          <NotifyBox checked={notify} onChange={setNotify} />
          <Button type="submit" size="sm" disabled={busy}>
            Proposer le déplacement
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>
            Fermer
          </Button>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="ghost" onClick={() => setMode("move")} disabled={busy}>
            Déplacer
          </Button>
          <Button size="sm" variant="ghost" onClick={() => cancel.mutate()} disabled={busy}>
            Annuler l&apos;événement
          </Button>
        </div>
      )}
      {error && <Alert tone="error">{errorMessage(error)}</Alert>}
    </div>
  );
}

function spanDays(start: string, end: string): number {
  return Math.max(1, Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000));
}

function NotifyBox({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 py-2 text-sm">
      <input
        type="checkbox"
        className="accent-teal-700"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      Prévenir les participants
    </label>
  );
}

/** Splits "a@x.org, b@y.org" into addresses (the server checks them). */
export function parseGuests(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((g) => g.trim())
    .filter(Boolean);
}

/** « Nouvel événement »: proposed in a calendar of the space that may be modified. */
export function NewEventForm({ spaceId, today }: { spaceId: string; today: string }) {
  const calendars = useQuery({
    queryKey: ["writable-calendars", spaceId],
    queryFn: () => api<WritableCalendar[]>(`/v1/calendar/calendars?space_id=${encodeURIComponent(spaceId)}`),
  });
  const [summary, setSummary] = useState("");
  const [day, setDay] = useState(today);
  const [allDay, setAllDay] = useState(false);
  const [from, setFrom] = useState("09:00");
  const [to, setTo] = useState("10:00");
  const [guests, setGuests] = useState("");
  const [notify, setNotify] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [approval, setApproval] = useState<Approval | null>(null);
  const propose = useMutation({
    mutationFn: () =>
      api<Approval>("/v1/calendar/events", {
        method: "POST",
        idempotent: true,
        body: {
          space_id: spaceId,
          ...(sourceId ? { source_id: sourceId } : {}),
          summary,
          ...(allDay
            ? { start_date: day, end_date: addDays(day, 1) }
            : { starts_at: localMoment(day, from), ends_at: localMoment(day, to) }),
          guests: parseGuests(guests),
          notify,
        },
      }),
    onSuccess: setApproval,
  });
  if (approval) {
    return (
      <Proposal
        approval={approval}
        onDone={() => {
          setApproval(null);
          setSummary("");
        }}
      />
    );
  }
  if (calendars.data && calendars.data.length === 0) {
    return (
      <p className="text-sm text-stone-500">
        Aucun agenda de cet espace ne peut être modifié. Autorisez « Modifier l&apos;agenda » dans Réglages &gt; Comptes
        connectés.
      </p>
    );
  }
  return (
    <form
      aria-label="Nouvel événement"
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        propose.mutate();
      }}
    >
      <Field label="Titre">
        <Input required maxLength={300} value={summary} onChange={(e) => setSummary(e.target.value)} />
      </Field>
      {calendars.data && calendars.data.length > 1 && (
        <Field label="Agenda">
          <Select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
            {calendars.data.map((c) => (
              <option key={c.source_id} value={c.source_id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Jour">
          <Input type="date" required value={day} onChange={(e) => setDay(e.target.value)} className="w-40" />
        </Field>
        {!allDay && (
          <>
            <Field label="Début">
              <Input type="time" required value={from} onChange={(e) => setFrom(e.target.value)} className="w-28" />
            </Field>
            <Field label="Fin">
              <Input type="time" required value={to} onChange={(e) => setTo(e.target.value)} className="w-28" />
            </Field>
          </>
        )}
        <label className="flex items-center gap-2 py-2 text-sm">
          <input
            type="checkbox"
            className="accent-teal-700"
            checked={allDay}
            onChange={(e) => setAllDay(e.target.checked)}
          />
          Toute la journée
        </label>
      </div>
      <Field label="Participants" hint="Adresses séparées par des virgules. Un événement avec participants demande votre passkey.">
        <Input value={guests} onChange={(e) => setGuests(e.target.value)} />
      </Field>
      {guests.trim() && <NotifyBox checked={notify} onChange={setNotify} />}
      {propose.error && <Alert tone="error">{errorMessage(propose.error)}</Alert>}
      <div>
        <Button type="submit" size="sm" disabled={propose.isPending}>
          Proposer l&apos;événement
        </Button>
      </div>
    </form>
  );
}
