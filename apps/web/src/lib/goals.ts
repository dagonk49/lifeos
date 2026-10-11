// Pure helpers for the goals page (F06): labels, French number formatting, form parsing.

import type { Goal, GoalProgress, GoalStatus, GoalSummary, Task } from "./types";

export type GoalKind = Goal["kind"];
export type ReviewCadence = Goal["review_cadence"];
export type GoalStatusFilter = GoalStatus | "all";
type Tone = "neutral" | "teal" | "amber" | "red" | "blue" | "violet";

export const GOAL_STATUS_LABELS: Record<GoalStatus, string> = {
  active: "Actif",
  paused: "En pause",
  achieved: "Atteint",
  abandoned: "Abandonné",
  archived: "Archivé",
};

export const GOAL_STATUS_TONES: Record<GoalStatus, Tone> = {
  active: "teal",
  paused: "amber",
  achieved: "blue",
  abandoned: "red",
  archived: "neutral",
};

export const GOAL_KIND_LABELS: Record<GoalKind, string> = {
  qualitative: "Qualitatif",
  measurable: "Mesurable",
};

export const CADENCE_LABELS: Record<ReviewCadence, string> = {
  weekly: "Hebdomadaire",
  monthly: "Mensuelle",
};

export const STATUS_FILTERS: { value: GoalStatusFilter; label: string; empty: string }[] = [
  { value: "active", label: "Actifs", empty: "Aucun objectif actif." },
  { value: "paused", label: "En pause", empty: "Aucun objectif en pause." },
  { value: "achieved", label: "Atteints", empty: "Aucun objectif atteint pour l'instant." },
  { value: "abandoned", label: "Abandonnés", empty: "Aucun objectif abandonné." },
  { value: "archived", label: "Archivés", empty: "Aucun objectif archivé." },
  { value: "all", label: "Tous", empty: "Aucun objectif pour l'instant." },
];

/** `GET /v1/goals` path for a space (null = all spaces) and a status filter. */
export function goalsPath(spaceId: string | null, filter: GoalStatusFilter): string {
  const params = new URLSearchParams();
  if (filter !== "all") params.set("status", filter);
  if (spaceId) params.set("space_id", spaceId);
  const query = params.toString();
  return query ? `/v1/goals?${query}` : "/v1/goals";
}

/** Active or paused: the goal is still being pursued. */
export function isOpenGoal(status: GoalStatus): boolean {
  return status === "active" || status === "paused";
}

export interface StatusAction {
  status: GoalStatus;
  label: string;
}

const ACTION_LABELS: Record<GoalStatus, string> = {
  active: "Réactiver",
  paused: "Mettre en pause",
  achieved: "Marquer comme atteint",
  abandoned: "Abandonner",
  archived: "Archiver",
};

const NEXT_STATUSES: Record<GoalStatus, GoalStatus[]> = {
  active: ["paused", "achieved", "abandoned", "archived"],
  paused: ["active", "achieved", "abandoned", "archived"],
  achieved: ["active", "archived"],
  abandoned: ["active", "archived"],
  archived: ["active"],
};

/** Status transitions offered from the current status. */
export function statusActions(status: GoalStatus): StatusAction[] {
  return NEXT_STATUSES[status].map((next) => ({ status: next, label: ACTION_LABELS[next] }));
}

// --- Numbers -------------------------------------------------------------------------

const NBSP = "\u00a0";
const numberFormat = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

/** API decimal string ("12.50") -> French display ("12,5 kg"). */
export function formatDecimal(value: string | null | undefined, unit?: string | null): string {
  if (value === null || value === undefined || value.trim() === "") return "";
  const number = Number(value);
  const text = Number.isFinite(number) ? numberFormat.format(number) : value;
  const suffix = unit?.trim();
  return suffix ? `${text}${NBSP}${suffix}` : text;
}

/** API decimal string -> editable French input value ("12.50" -> "12,5"). */
export function decimalToInput(value: string | null | undefined): string {
  if (!value) return "";
  const trimmed = value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
  return trimmed.replace(".", ",");
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

const DECIMAL = /^[-+]?(\d{1,12}(\.\d{0,2})?|\.\d{1,2})$/;

/**
 * French user input -> API decimal string (14 digits, 2 decimals at most).
 * Accepts a comma or a dot and spaces as thousands separators; "" -> null.
 */
export function parseDecimal(raw: string): Parsed<string | null> {
  const compact = raw.replace(/[\s\u00a0\u202f]/g, "").replace(",", ".");
  if (compact === "") return { ok: true, value: null };
  if (!DECIMAL.test(compact)) {
    return { ok: false, error: "Nombre invalide : 12 chiffres et 2 décimales au plus." };
  }
  let value = compact.replace(/^\+/, "").replace(/\.$/, "");
  value = value.replace(/^(-?)\./, "$10.");
  return { ok: true, value };
}

// --- Progress ------------------------------------------------------------------------

/** 0..100, or null when there is nothing to measure. */
export function progressPercent(ratio: number | null): number | null {
  if (ratio === null || !Number.isFinite(ratio)) return null;
  return Math.round(Math.min(1, Math.max(0, ratio)) * 100);
}

export function formatPercent(ratio: number | null): string {
  const percent = progressPercent(ratio);
  return percent === null ? "" : `${percent}${NBSP}%`;
}

type ProgressSource = Pick<GoalSummary, "current_value" | "target_value" | "metric_unit"> & {
  progress: Pick<GoalProgress, "basis" | "milestones_done" | "milestones_total">;
};

export function progressLabel(goal: ProgressSource): string {
  const { progress } = goal;
  if (progress.basis === "metric") {
    return `${formatDecimal(goal.current_value)} / ${formatDecimal(goal.target_value, goal.metric_unit)}`;
  }
  if (progress.basis === "milestones") {
    const noun = progress.milestones_total > 1 ? "jalons" : "jalon";
    return `${progress.milestones_done}/${progress.milestones_total} ${noun}`;
  }
  return "Rien à mesurer pour l'instant";
}

/** The measure says "done" but the goal is still open: suggest marking it achieved. */
export function suggestAchieved(goal: Pick<GoalSummary, "status" | "progress">): boolean {
  return (
    isOpenGoal(goal.status) &&
    goal.progress.basis !== "none" &&
    goal.progress.ratio !== null &&
    goal.progress.ratio >= 1
  );
}

export function tasksLabel(open: number, done: number): string {
  if (open === 0 && done === 0) return "Aucune tâche liée";
  const doneText = done > 1 ? `${done} terminées` : "1 terminée";
  if (open === 0) return done > 1 ? `${done} tâches terminées` : "1 tâche terminée";
  const openText = open > 1 ? `${open} tâches ouvertes` : "1 tâche ouverte";
  return done === 0 ? openText : `${openText} · ${doneText}`;
}

// --- Dates ---------------------------------------------------------------------------

function dayNumber(date: string): number {
  const [year, month, day] = date.slice(0, 10).split("-").map(Number);
  return Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1) / 86_400_000;
}

/** Relative hint for a calendar due date ("dans 3 jours", "en retard de 2 jours"). */
export function dueHint(due: string | null, today: string): { text: string; overdue: boolean } | null {
  if (!due) return null;
  const days = Math.round(dayNumber(due) - dayNumber(today));
  if (days === 0) return { text: "aujourd'hui", overdue: false };
  if (days === 1) return { text: "demain", overdue: false };
  if (days > 1) return { text: `dans ${days} jours`, overdue: false };
  if (days === -1) return { text: "en retard d'un jour", overdue: true };
  return { text: `en retard de ${-days} jours`, overdue: true };
}

// --- Linked tasks --------------------------------------------------------------------

const OPEN_TASK = new Set(["todo", "in_progress"]);

/** Open tasks first (by due date, then newest), then done and cancelled ones. */
export function sortGoalTasks(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    const openA = OPEN_TASK.has(a.status);
    const openB = OPEN_TASK.has(b.status);
    if (openA !== openB) return openA ? -1 : 1;
    if (openA) {
      if (a.due_at && b.due_at && a.due_at !== b.due_at) return a.due_at.localeCompare(b.due_at);
      if (a.due_at && !b.due_at) return -1;
      if (!a.due_at && b.due_at) return 1;
      return b.created_at.localeCompare(a.created_at);
    }
    return (b.completed_at ?? b.updated_at).localeCompare(a.completed_at ?? a.updated_at);
  });
}

// --- Forms ---------------------------------------------------------------------------

export interface GoalFormValues {
  title: string;
  kind: GoalKind;
  metric_unit: string;
  start_value: string;
  target_value: string;
  start_date: string;
  due_date: string;
  motivation: string;
  description: string;
  review_cadence: ReviewCadence;
}

export const EMPTY_GOAL_FORM: GoalFormValues = {
  title: "",
  kind: "qualitative",
  metric_unit: "",
  start_value: "",
  target_value: "",
  start_date: "",
  due_date: "",
  motivation: "",
  description: "",
  review_cadence: "weekly",
};

export function goalToForm(goal: Goal): GoalFormValues {
  return {
    title: goal.title,
    kind: goal.kind,
    metric_unit: goal.metric_unit ?? "",
    start_value: decimalToInput(goal.start_value),
    target_value: decimalToInput(goal.target_value),
    start_date: goal.start_date ?? "",
    due_date: goal.due_date ?? "",
    motivation: goal.motivation ?? "",
    description: goal.description ?? "",
    review_cadence: goal.review_cadence,
  };
}

export interface GoalFields {
  title: string;
  description: string | null;
  motivation: string | null;
  metric_unit: string | null;
  start_value: string | null;
  target_value: string | null;
  start_date: string | null;
  due_date: string | null;
  review_cadence: ReviewCadence;
}

export interface GoalCreateBody extends GoalFields {
  space_id: string;
  kind: GoalKind;
}

const blankToNull = (value: string): string | null => value.trim() || null;

/** Validates the shared fields (same rules as the API) and normalises them. */
function parseGoalFields(form: GoalFormValues): Parsed<GoalFields> {
  const title = form.title.trim();
  if (!title) return { ok: false, error: "Donnez un intitulé à l'objectif." };
  let metric: Pick<GoalFields, "metric_unit" | "start_value" | "target_value"> = {
    metric_unit: null,
    start_value: null,
    target_value: null,
  };
  if (form.kind === "measurable") {
    const start = parseDecimal(form.start_value);
    if (!start.ok) return { ok: false, error: `Valeur de départ : ${start.error}` };
    const target = parseDecimal(form.target_value);
    if (!target.ok) return { ok: false, error: `Valeur cible : ${target.error}` };
    if (target.value === null) {
      return { ok: false, error: "Un objectif mesurable demande une valeur cible." };
    }
    metric = { metric_unit: blankToNull(form.metric_unit), start_value: start.value, target_value: target.value };
  }
  const start_date = form.start_date || null;
  const due_date = form.due_date || null;
  if (start_date && due_date && due_date < start_date) {
    return { ok: false, error: "L'échéance doit être postérieure à la date de début." };
  }
  return {
    ok: true,
    value: {
      title,
      description: blankToNull(form.description),
      motivation: blankToNull(form.motivation),
      ...metric,
      start_date,
      due_date,
      review_cadence: form.review_cadence,
    },
  };
}

export function buildGoalCreate(form: GoalFormValues, spaceId: string): Parsed<GoalCreateBody> {
  const fields = parseGoalFields(form);
  if (!fields.ok) return fields;
  return { ok: true, value: { space_id: spaceId, kind: form.kind, ...fields.value } };
}

function sameDecimal(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return a === b;
  return Number(a) === Number(b);
}

/** PATCH body with only the fields that changed (empty object: nothing to save). */
export function buildGoalUpdate(goal: Goal, form: GoalFormValues): Parsed<Partial<GoalFields>> {
  const fields = parseGoalFields({ ...form, kind: goal.kind });
  if (!fields.ok) return fields;
  const next = fields.value;
  const body: Partial<GoalFields> = {};
  if (next.title !== goal.title) body.title = next.title;
  if (next.description !== goal.description) body.description = next.description;
  if (next.motivation !== goal.motivation) body.motivation = next.motivation;
  if (next.start_date !== goal.start_date) body.start_date = next.start_date;
  if (next.due_date !== goal.due_date) body.due_date = next.due_date;
  if (next.review_cadence !== goal.review_cadence) body.review_cadence = next.review_cadence;
  if (goal.kind === "measurable") {
    if (next.metric_unit !== goal.metric_unit) body.metric_unit = next.metric_unit;
    if (!sameDecimal(next.start_value, goal.start_value)) body.start_value = next.start_value;
    if (!sameDecimal(next.target_value, goal.target_value)) body.target_value = next.target_value;
  }
  return { ok: true, value: body };
}

export interface CheckinBody {
  value: string | null;
  note: string | null;
}

/** A check-in needs a value (measurable goals only) or a note. */
export function buildCheckin(kind: GoalKind, rawValue: string, rawNote: string): Parsed<CheckinBody> {
  let value: string | null = null;
  if (kind === "measurable") {
    const parsed = parseDecimal(rawValue);
    if (!parsed.ok) return parsed;
    value = parsed.value;
  }
  const note = blankToNull(rawNote);
  if (value === null && note === null) {
    return {
      ok: false,
      error: kind === "measurable" ? "Indiquez une valeur ou une note." : "Écrivez une note.",
    };
  }
  return { ok: true, value: { value, note } };
}

// --- Query keys ----------------------------------------------------------------------

/** Every goal query starts with "goals": invalidating ["goals"] refreshes list and detail. */
export const goalKeys = {
  all: ["goals"] as const,
  lists: ["goals", "list"] as const,
  list: (spaceId: string | null, filter: GoalStatusFilter) => ["goals", "list", spaceId, filter] as const,
  detail: (id: string) => ["goals", "detail", id] as const,
  /** Under "tasks" so that any task mutation elsewhere refreshes it too. */
  tasks: (id: string) => ["tasks", "goal", id] as const,
};
