import type { Task, TaskBlocker } from "./types";

/** Same limits as the API (backend modules/tasks/models.py). */
export const MAX_TAGS = 10;
export const MAX_TAG_LENGTH = 40;

/** « Bloquée par « A » », « … « A » et « B » », « … « A », « B » et 2 autres ». */
export function blockedLabel(blockers: TaskBlocker[]): string | null {
  if (blockers.length === 0) return null;
  const shown = blockers.slice(0, 2).map((b) => `« ${b.title} »`);
  const rest = blockers.length - shown.length;
  if (rest > 0) return `Bloquée par ${shown.join(", ")} et ${rest === 1 ? "1 autre" : `${rest} autres`}`;
  return `Bloquée par ${shown.join(" et ")}`;
}

/** Why a task cannot be started or finished, for the API's 409 task_blocked too. */
export function blockedHint(blockers: TaskBlocker[]): string | null {
  const label = blockedLabel(blockers);
  return label ? `${label} : terminez ou annulez d'abord ${blockers.length > 1 ? "ces tâches" : "cette tâche"}.` : null;
}

/** The API's normalisation: no leading '#', single spaces, lower case. */
export function normalizeTag(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(" ").replace(/^#+/, "").trim().toLowerCase();
}

export type TagsResult = { ok: true; tags: string[] } | { ok: false; error: string };

/** Comma-separated labels as typed in the form. */
export function parseTags(input: string): TagsResult {
  const tags: string[] = [];
  for (const part of input.split(",")) {
    const tag = normalizeTag(part);
    if (!tag) continue;
    if (tag.length > MAX_TAG_LENGTH) {
      return { ok: false, error: `Une étiquette fait au plus ${MAX_TAG_LENGTH} caractères.` };
    }
    if (!tags.includes(tag)) tags.push(tag);
  }
  if (tags.length > MAX_TAGS) return { ok: false, error: `${MAX_TAGS} étiquettes au plus.` };
  return { ok: true, tags };
}

/** Free tasks first, then by due date, then the most recent; blocked ones last. */
export function sortTasks(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if (a.blocked !== b.blocked) return a.blocked ? 1 : -1;
    if (a.due_at && b.due_at) return a.due_at.localeCompare(b.due_at);
    if (a.due_at) return -1;
    if (b.due_at) return 1;
    return b.created_at.localeCompare(a.created_at);
  });
}

/** Open tasks of the same space that `task` (or a new task) could wait for. */
export function dependencyCandidates(tasks: Task[], spaceId: string, task?: Task): Task[] {
  return tasks
    .filter(
      (t) =>
        t.space_id === spaceId &&
        (t.status === "todo" || t.status === "in_progress") &&
        t.id !== task?.id &&
        !task?.depends_on.includes(t.id),
    )
    .sort((a, b) => a.title.localeCompare(b.title, "fr"));
}

/** Every label used by these tasks, for the filter. */
export function knownTags(tasks: Task[]): string[] {
  return [...new Set(tasks.flatMap((t) => t.tags))].sort((a, b) => a.localeCompare(b, "fr"));
}
