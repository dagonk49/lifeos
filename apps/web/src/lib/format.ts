const dateFormat = new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium" });
const dateTimeFormat = new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" });
const timeFormat = new Intl.DateTimeFormat("fr-FR", { timeStyle: "short" });

export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  // Calendar dates (YYYY-MM-DD) are local dates, not instants.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
  return dateFormat.format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  return value ? dateTimeFormat.format(new Date(value)) : "";
}

export function formatTime(value: string | null | undefined): string {
  return value ? timeFormat.format(new Date(value)) : "";
}

/** `<input type="datetime-local">` value (local time) -> ISO instant, or null. */
export function localInputToIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

/** ISO instant -> `<input type="datetime-local">` value in local time. */
export function isoToLocalInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function todayLocal(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export interface SnippetPart {
  text: string;
  highlight: boolean;
}

/** Splits a server snippet where matches are delimited by « and ». Never renders HTML. */
export function splitSnippet(snippet: string): SnippetPart[] {
  const parts: SnippetPart[] = [];
  const pattern = /«([^»]*)»/g;
  let last = 0;
  for (const match of snippet.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: snippet.slice(last, index), highlight: false });
    parts.push({ text: match[1] ?? "", highlight: true });
    last = index + match[0].length;
  }
  if (last < snippet.length) parts.push({ text: snippet.slice(last), highlight: false });
  return parts;
}

export const ORIGIN_LABELS: Record<string, string> = {
  user_declared: "Déclaré",
  user_confirmed: "Confirmé",
  extracted: "Extrait",
  inferred: "Inféré",
};

export const KIND_LABELS: Record<string, string> = {
  fact: "Fait",
  event: "Événement",
  preference: "Préférence",
  decision: "Décision",
};

export const STATUS_LABELS: Record<string, string> = {
  todo: "À faire",
  in_progress: "En cours",
  done: "Terminée",
  cancelled: "Annulée",
};

export const PRIORITY_LABELS: Record<string, string> = {
  low: "Basse",
  normal: "Normale",
  high: "Haute",
};
