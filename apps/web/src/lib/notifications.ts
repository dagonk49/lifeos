import type {
  NotificationCategory,
  NotificationCategoryPreference,
  NotificationItem,
  NotificationWindow,
} from "@/lib/types";

// Mirror of notifications/router.py (MAX_WINDOWS, MAX_DIGEST_TIMES, max_per_day 1..100).
export const MAX_WINDOWS = 4;
export const MAX_DIGEST_TIMES = 4;
export const MAX_PER_DAY = 100;

export const CATEGORY_LABELS: Record<NotificationCategory, string> = {
  important: "Importantes (rappels, tâches débloquées, comptes à reconnecter)",
  suggestion: "Suggestions (bilans prêts…)",
};

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export function isTime(value: string): boolean {
  return HHMM.test(value);
}

/** Why these delivery windows are refused, or null when they are valid. */
export function windowsError(windows: NotificationWindow[]): string | null {
  if (windows.length > MAX_WINDOWS) return `Au plus ${MAX_WINDOWS} créneaux.`;
  if (windows.some((w) => !isTime(w.start) || !isTime(w.end))) return "Indiquez l'heure de début et de fin de chaque créneau.";
  if (windows.some((w) => w.start >= w.end)) return "Chaque créneau doit finir après son début, le même jour.";
  const sorted = [...windows].sort((a, b) => a.start.localeCompare(b.start));
  if (sorted.some((w, i) => i > 0 && w.start < (sorted[i - 1]?.end ?? ""))) {
    return "Les créneaux ne doivent pas se chevaucher.";
  }
  return null;
}

/** Why these digest times are refused, or null when they are valid. */
export function digestTimesError(times: string[]): string | null {
  if (times.length === 0) return "Gardez au moins une heure de regroupement.";
  if (times.length > MAX_DIGEST_TIMES) return `Au plus ${MAX_DIGEST_TIMES} heures de regroupement.`;
  if (times.some((t) => !isTime(t))) return "Indiquez chaque heure au format HH:MM.";
  if (new Set(times).size !== times.length) return "Chaque heure ne peut figurer qu'une fois.";
  return null;
}

/** "" means no limit; anything else must be a whole number from 1 to 100. */
export function parseMaxPerDay(value: string): number | null | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isInteger(n) && n >= 1 && n <= MAX_PER_DAY ? n : undefined;
}

/** Every error of one category's form (empty when it can be saved). */
export function preferenceErrors(pref: NotificationCategoryPreference, maxPerDay: string): string[] {
  const errors: string[] = [];
  if (parseMaxPerDay(maxPerDay) === undefined) errors.push(`Le maximum par jour va de 1 à ${MAX_PER_DAY} (vide : sans limite).`);
  const specific = pref.category === "important" ? windowsError(pref.windows) : digestTimesError(pref.digest_times);
  if (specific) errors.push(specific);
  return errors;
}

export function isDigest(notification: NotificationItem): boolean {
  return notification.source_type === "digest";
}

export function digestSummary(notification: NotificationItem): string {
  const n = notification.items_count;
  return n === 1 ? "1 suggestion" : `${n} suggestions`;
}
