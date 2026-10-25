// Pure helpers of the agenda (task 14). Dates are "YYYY-MM-DD" strings in the owner's time
// zone: the server writes every instant with the offset of that zone, so the local day and
// time are the first characters of the string, whatever the browser's own zone.

import type { CalendarEvent } from "./types";

const DAY_MS = 86_400_000;

function toUtc(day: string): number {
  const [y = 1970, m = 1, d = 1] = day.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function fromUtc(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(day: string, days: number): string {
  return fromUtc(toUtc(day) + days * DAY_MS);
}

/** Monday of the week of `day` (weeks start on Monday, as in France). */
export function mondayOf(day: string): string {
  const weekday = (new Date(toUtc(day)).getUTCDay() + 6) % 7; // Monday = 0
  return addDays(day, -weekday);
}

export function weekDays(monday: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** Query of GET /v1/calendar/events for [first, last + 1 day) in the owner's zone. */
export function rangeQuery(first: string | undefined, days: number, spaceId?: string | null): string {
  if (!first) throw new Error("no day");
  const params = new URLSearchParams({ start: `${first}T00:00:00`, end: `${addDays(first, days)}T00:00:00` });
  if (spaceId) params.set("space_id", spaceId);
  return `/v1/calendar/events?${params.toString()}`;
}

/** Local days an occurrence covers (an end at midnight does not touch the next day). */
export function eventDays(event: CalendarEvent): string[] {
  let first: string;
  let last: string;
  if (event.all_day && event.start_date && event.end_date) {
    first = event.start_date;
    last = addDays(event.end_date, -1);
  } else if (event.starts_at && event.ends_at) {
    first = event.starts_at.slice(0, 10);
    last = event.ends_at.slice(0, 10);
    if (last > first && event.ends_at.slice(11, 19) === "00:00:00") last = addDays(last, -1);
  } else {
    return [];
  }
  const days: string[] = [];
  for (let day = first; day <= last && days.length < 62; day = addDays(day, 1)) days.push(day);
  return days;
}

/** Occurrences of each day, all-day ones first, then by start time. */
export function groupByDay(events: readonly CalendarEvent[], days: readonly string[]): Map<string, CalendarEvent[]> {
  const groups = new Map<string, CalendarEvent[]>(days.map((d) => [d, []]));
  for (const event of events) {
    for (const day of eventDays(event)) groups.get(day)?.push(event);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => {
      if (a.all_day !== b.all_day) return a.all_day ? -1 : 1;
      return (a.starts_at ?? "").localeCompare(b.starts_at ?? "") || a.summary.localeCompare(b.summary);
    });
  }
  return groups;
}

/** "Toute la journée", "09:30 – 10:00", or "jusqu'à 10:00" / "à partir de 22:00" on the
 * days a long occurrence only starts or ends. */
export function timeLabel(event: CalendarEvent, day: string): string {
  if (event.all_day || !event.starts_at || !event.ends_at) return "Toute la journée";
  const startDay = event.starts_at.slice(0, 10);
  const endDay = event.ends_at.slice(0, 10);
  const start = event.starts_at.slice(11, 16);
  const end = event.ends_at.slice(11, 16);
  if (startDay === day && endDay === day) return start === end ? start : `${start} – ${end}`;
  if (startDay === day) return `à partir de ${start}`;
  if (endDay === day && end !== "00:00") return `jusqu'à ${end}`;
  return "Toute la journée";
}

const WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const MONTHS = [
  "janvier",
  "février",
  "mars",
  "avril",
  "mai",
  "juin",
  "juillet",
  "août",
  "septembre",
  "octobre",
  "novembre",
  "décembre",
];

/** "lundi 19 octobre". */
export function dayLabel(day: string): string {
  const date = new Date(toUtc(day));
  const weekday = WEEKDAYS[(date.getUTCDay() + 6) % 7];
  return `${weekday} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

/** "Semaine du 19 au 25 octobre 2026". */
export function weekLabel(monday: string): string {
  const start = new Date(toUtc(monday));
  const end = new Date(toUtc(addDays(monday, 6)));
  const year = end.getUTCFullYear();
  const startMonth = start.getUTCMonth() === end.getUTCMonth() ? "" : ` ${MONTHS[start.getUTCMonth()]}`;
  return `Semaine du ${start.getUTCDate()}${startMonth} au ${end.getUTCDate()} ${MONTHS[end.getUTCMonth()]} ${year}`;
}

const RESPONSES: Record<string, string> = {
  needsAction: "Réponse en attente",
  tentative: "Peut-être",
  declined: "Refusé",
};

/** The owner's answer to an invitation, when worth showing. */
export function responseLabel(event: CalendarEvent): string | null {
  return event.response ? (RESPONSES[event.response] ?? null) : null;
}

const SOURCE_ERRORS: Record<string, string> = {
  consent_missing: "Lecture de l'agenda non autorisée dans cet espace : synchronisation arrêtée.",
  scope_missing: "Lecture de l'agenda non accordée chez le fournisseur.",
  account_inactive: "Compte à reconnecter : synchronisation arrêtée.",
  calendar_gone: "Agenda introuvable chez le fournisseur (supprimé ou plus partagé).",
  rate_limited: "Trop de requêtes chez le fournisseur : nouvel essai automatique.",
  too_many_pages: "Agenda très chargé : synchronisation en plusieurs fois.",
  provider_unavailable: "Fournisseur non configuré sur ce serveur.",
};

/** Why the last sync of a followed calendar failed, in French. */
export function sourceErrorMessage(code: string | null): string | null {
  if (!code) return null;
  return SOURCE_ERRORS[code] ?? "Fournisseur injoignable : nouvel essai automatique.";
}
