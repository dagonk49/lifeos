// Pure helpers for the reviews page (F05): French labels, period arithmetic, notices.
// No React here so that everything is unit-testable.

import { problemMessage } from "./api";
import type { LLMTier, Review, ReviewFacts, ReviewPeriod } from "./types";

export const PERIOD_OPTIONS: { value: ReviewPeriod; label: string }[] = [
  { value: "daily", label: "Jour" },
  { value: "weekly", label: "Semaine" },
  { value: "monthly", label: "Mois" },
];

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

const WEEKDAYS = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];

const DAY_MS = 86_400_000;

interface CalendarDate {
  year: number;
  /** 1..12 */
  month: number;
  day: number;
}

/** "YYYY-MM-DD" -> calendar date, or null when malformed or impossible (2026-02-30). */
export function parseCalendarDate(value: string): CalendarDate | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null;
  }
  return { year, month, day };
}

function toUtc(date: CalendarDate): Date {
  return new Date(Date.UTC(date.year, date.month - 1, date.day));
}

function toIso(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

function monthName(date: CalendarDate): string {
  return MONTHS[date.month - 1] ?? "";
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** French day number: "1er" for the first day of the month. */
export function dayNumber(day: number): string {
  return day === 1 ? "1er" : String(day);
}

/** "2026-10-11" -> "dimanche 11 octobre 2026" (lower case, for use inside a sentence). */
export function formatLongDate(value: string): string {
  const date = parseCalendarDate(value);
  if (!date) return value;
  const weekday = WEEKDAYS[toUtc(date).getUTCDay()] ?? "";
  return `${weekday} ${dayNumber(date.day)} ${monthName(date)} ${date.year}`;
}

function rangeText(start: CalendarDate, end: CalendarDate): string {
  if (start.year !== end.year) {
    return `du ${dayNumber(start.day)} ${monthName(start)} ${start.year} au ${dayNumber(end.day)} ${monthName(end)} ${end.year}`;
  }
  if (start.month !== end.month) {
    return `du ${dayNumber(start.day)} ${monthName(start)} au ${dayNumber(end.day)} ${monthName(end)} ${end.year}`;
  }
  return `du ${dayNumber(start.day)} au ${dayNumber(end.day)} ${monthName(end)} ${end.year}`;
}

/**
 * Human label of a review period:
 * "Dimanche 11 octobre 2026", "Semaine du 5 au 11 octobre 2026", "Octobre 2026".
 */
export function periodLabel(period: string, start: string, end: string): string {
  const s = parseCalendarDate(start);
  const e = parseCalendarDate(end);
  if (!s || !e) return `${start} – ${end}`;
  switch (period) {
    case "daily":
      return capitalize(formatLongDate(start));
    case "weekly":
      return `Semaine ${rangeText(s, e)}`;
    case "monthly":
      return capitalize(`${monthName(s)} ${s.year}`);
    default:
      return capitalize(rangeText(s, e));
  }
}

/** Mirror of the backend `period_bounds`: weeks run Monday to Sunday. */
export function periodBounds(period: ReviewPeriod, day: string): { start: string; end: string } | null {
  const date = parseCalendarDate(day);
  if (!date) return null;
  if (period === "daily") return { start: day, end: day };
  if (period === "weekly") {
    const utc = toUtc(date);
    const offset = (utc.getUTCDay() + 6) % 7;
    const start = new Date(utc.getTime() - offset * DAY_MS);
    return { start: toIso(start), end: toIso(new Date(start.getTime() + 6 * DAY_MS)) };
  }
  const start = new Date(Date.UTC(date.year, date.month - 1, 1));
  const end = new Date(Date.UTC(date.year, date.month, 0));
  return { start: toIso(start), end: toIso(end) };
}

/** Whole days from `from` to `to` (negative if `to` is earlier), or null if malformed. */
export function daysBetween(from: string, to: string): number | null {
  const a = parseCalendarDate(from);
  const b = parseCalendarDate(to);
  if (!a || !b) return null;
  return Math.round((toUtc(b).getTime() - toUtc(a).getTime()) / DAY_MS);
}

/** Calendar date ("YYYY-MM-DD") of an instant in an IANA time zone, or null. */
export function localDateIn(instant: string, timeZone: string): string | null {
  const date = new Date(instant);
  if (Number.isNaN(date.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value;
    const year = get("year");
    const month = get("month");
    const day = get("day");
    return year && month && day ? `${year}-${month}-${day}` : null;
  } catch {
    return null; // Unknown time zone.
  }
}

/**
 * Days of the period covered by the facts: the whole period, or the days elapsed
 * (including the day of computation) when the period was still in progress.
 */
export function coveredDays(period: ReviewFacts["period"]): { days: number; total: number } {
  const total = Math.max(1, (daysBetween(period.start, period.end) ?? 0) + 1);
  if (!period.partial) return { days: total, total };
  const computedOn = localDateIn(period.computed_at, period.timezone);
  const elapsed = computedOn ? daysBetween(period.start, computedOn) : null;
  if (elapsed === null) return { days: total, total };
  return { days: Math.min(total, Math.max(1, elapsed + 1)), total };
}

// --- Who generated the review, narrative status -----------------------------------------

export function isAutomatic(generatedBy: string): boolean {
  return generatedBy.startsWith("agent:");
}

export function generatedByLabel(generatedBy: string): string {
  if (generatedBy === "user") return "Par vous";
  if (isAutomatic(generatedBy)) return "Automatique";
  return generatedBy;
}

/** Model tier used for the narrative (backend `_narrative`): daily is cheap, others "deep". */
export function narrativeTier(period: ReviewPeriod): LLMTier {
  return period === "daily" ? "fast" : "deep";
}

export const NARRATIVE_BADGES: Record<string, { label: string; tone: "violet" | "neutral" | "amber" }> = {
  generated: { label: "Lecture de l'assistant", tone: "violet" },
  none: { label: "Faits seuls", tone: "neutral" },
  unavailable: { label: "Lecture indisponible", tone: "amber" },
};

export function narrativeBadge(status: string): { label: string; tone: "violet" | "neutral" | "amber" } {
  return NARRATIVE_BADGES[status] ?? { label: "Faits seuls", tone: "neutral" };
}

/** French fallbacks for narrative error codes that have no message in api.ts. */
const NARRATIVE_ERROR_FALLBACKS: Record<string, string> = {
  llm_error: "Le fournisseur IA a renvoyé une erreur.",
};

/** Codes for which the owner can act in the settings page. */
const SETTINGS_CODES = new Set(["cloud_consent_required", "llm_paused", "llm_budget_exceeded"]);

export interface NarrativeNotice {
  tone: "info" | "warning";
  text: string;
  /** Show a link to the settings page. */
  settings: boolean;
}

/** Why a review has no assistant reading (from `narrative_status` / `narrative_error`). */
export function narrativeNotice(
  review: Pick<Review, "narrative_status" | "narrative_error" | "generated_by" | "period">,
): NarrativeNotice {
  if (review.narrative_status === "unavailable") {
    const code = review.narrative_error ?? "llm_error";
    const reason = problemMessage(
      code,
      NARRATIVE_ERROR_FALLBACKS[code] ?? `Erreur inattendue (code « ${code} »).`,
    );
    return {
      tone: "warning",
      text: `La lecture de l'assistant n'a pas pu être rédigée. ${reason} Les faits restent complets ; établissez ce bilan à nouveau plus tard pour obtenir la lecture.`,
      settings: SETTINGS_CODES.has(code),
    };
  }
  if (review.narrative_status === "none") {
    if (isAutomatic(review.generated_by) && review.period === "daily") {
      return {
        tone: "info",
        text: "Les bilans quotidiens automatiques restent locaux : aucune requête n'est envoyée à l'IA.",
        settings: false,
      };
    }
    if (isAutomatic(review.generated_by)) {
      return { tone: "info", text: "Ce bilan automatique a été établi sans la lecture de l'assistant.", settings: false };
    }
    return {
      tone: "info",
      text: "Ce bilan a été établi sans la lecture de l'assistant (option décochée). Établissez-le à nouveau avec l'option cochée pour l'obtenir.",
      settings: false,
    };
  }
  return { tone: "info", text: "Aucune lecture de l'assistant n'est disponible pour ce bilan.", settings: false };
}

// --- Facts sections -------------------------------------------------------------------------

/** Readable resources (permissions, omitted sections of the facts). */
export const RESOURCE_LABELS: Record<string, string> = {
  tasks: "Tâches",
  journal: "Journal",
  goals: "Objectifs",
  facts: "Faits (mémoire)",
  memories: "Souvenirs",
  messages: "Conversations avec l'assistant",
};

export function resourceLabel(resource: string): string {
  return RESOURCE_LABELS[resource] ?? humanizeKey(resource);
}

export const SECTION_LABELS: Record<string, string> = {
  tasks: "Tâches",
  journal: "Journal",
  goals: "Objectifs actifs",
  memory: "Mémoire",
  assistant: "Assistant",
};

export const KNOWN_SECTIONS = ["tasks", "journal", "goals", "memory", "assistant"] as const;
const RESERVED_KEYS = new Set<string>(["period", "omitted", ...KNOWN_SECTIONS]);

export function sectionLabel(key: string): string {
  return SECTION_LABELS[key] ?? humanizeKey(key);
}

/** Sections of the facts this page does not know (rendered generically), in order. */
export function otherSections(facts: Record<string, unknown>): string[] {
  return Object.keys(facts).filter((key) => !RESERVED_KEYS.has(key) && facts[key] !== undefined);
}

/** Keys of `value` not in `known` (new backend fields, rendered generically). */
export function extraKeys(value: Record<string, unknown>, known: readonly string[]): string[] {
  return Object.keys(value).filter((key) => !known.includes(key));
}

/** "due_next_7_days" -> "Due next 7 days" (labels for unknown keys). */
export function humanizeKey(key: string): string {
  const text = key.replace(/[_-]+/g, " ").trim();
  return text ? capitalize(text) : key;
}

export const GOAL_KIND_LABELS: Record<string, string> = {
  measurable: "Mesurable",
  qualitative: "Qualitatif",
};

// --- Numbers ---------------------------------------------------------------------------------

/** Decimal (number or "12.50" string) in French notation, or "" when not a number. */
export function formatDecimal(value: string | number | null | undefined, maxDigits = 2): string {
  if (value === null || value === undefined || value === "") return "";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "";
  return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: maxDigits }).format(number);
}

/** 0.456 -> "46 %" (with a no-break space). */
export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)} %`;
}

/** "12,5 / 20 km", "12,5 km", "" (goal metric). */
export function goalValueText(current: unknown, target: unknown, unit: unknown): string {
  const c = typeof current === "string" || typeof current === "number" ? formatDecimal(current) : "";
  const t = typeof target === "string" || typeof target === "number" ? formatDecimal(target) : "";
  const u = typeof unit === "string" && unit ? ` ${unit}` : "";
  if (c && t) return `${c} / ${t}${u}`;
  if (c) return `${c}${u}`;
  if (t) return `Cible : ${t}${u}`;
  return "";
}

/** French plural: 0 and 1 take the singular. */
export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count >= 2 ? pluralForm : singular}`;
}

/** "et 3 autres" when a list was truncated by the backend, else "". */
export function andMore(total: number, shown: number): string {
  const rest = total - shown;
  if (rest <= 0) return "";
  return rest === 1 ? "et 1 autre" : `et ${rest} autres`;
}

// --- Type guards for the free-form facts JSON ------------------------------------------------

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

// --- Automation ------------------------------------------------------------------------------

/** Schedule of automatic reviews (backend workers/scheduler.py, Europe/Paris time). */
export const AUTOMATIC_SCHEDULE: { period: ReviewPeriod; when: string; what: string }[] = [
  {
    period: "daily",
    when: "Chaque soir à 21 h 30",
    what: "bilan du jour, calculé localement : aucune requête à l'IA",
  },
  {
    period: "weekly",
    when: "Le dimanche à 19 h",
    what: "bilan de la semaine, avec la lecture de l'assistant (une requête « deep »)",
  },
  {
    period: "monthly",
    when: "Le 1er de chaque mois à 8 h",
    what: "bilan du mois, avec la lecture de l'assistant (une requête « deep »)",
  },
];

/** The review of the same period, if one already exists (generating again replaces it). */
export function findSamePeriod(
  reviews: readonly Review[] | undefined,
  period: ReviewPeriod,
  day: string,
): Review | undefined {
  const bounds = periodBounds(period, day);
  if (!bounds || !reviews) return undefined;
  return reviews.find((r) => r.period === period && r.period_start === bounds.start);
}
