// Pure helpers for « Discuter de… » (task 24): the task, goal, journal entry or review the
// owner explicitly chooses as the subject of a message. Carried by the URL
// (/chat?about=goal:<uuid>) and sent as `context.ref`; the server checks it again (same
// space, rights) and sends it as data, never as instructions.

import { formatDate } from "./format";
import { periodLabel } from "./reviews";
import type { AgentInfo, ChatContextRef, ChatContextType } from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TYPES: readonly ChatContextType[] = ["task", "goal", "journal", "review"];

/** The resource an agent must read in the space (server: chat/context_ref.py RESOURCES). */
type ContextResource = "tasks" | "goals" | "journal" | "reviews";

const NAMES: Record<
  ChatContextType,
  { label: string; linked: string; missing: string; resource: ContextResource; plural: string }
> = {
  task: { label: "Tâche", linked: "Tâche liée", missing: "Tâche introuvable ou supprimée", resource: "tasks", plural: "tâches" },
  goal: {
    label: "Objectif",
    linked: "Objectif lié",
    missing: "Objectif introuvable ou supprimé",
    resource: "goals",
    plural: "objectifs",
  },
  journal: {
    label: "Entrée de journal",
    linked: "Entrée liée",
    missing: "Entrée de journal introuvable ou supprimée",
    resource: "journal",
    plural: "entrées de journal",
  },
  review: { label: "Bilan", linked: "Bilan lié", missing: "Bilan introuvable ou supprimé", resource: "reviews", plural: "bilans" },
};

/** `<type>:<uuid>`; anything else (unknown type, not a UUID) is ignored. */
export function parseAbout(param: string | null | undefined): ChatContextRef | null {
  if (!param) return null;
  const separator = param.indexOf(":");
  if (separator < 0) return null;
  const type = param.slice(0, separator);
  const id = param.slice(separator + 1);
  if (!(TYPES as readonly string[]).includes(type) || !UUID.test(id)) return null;
  return { type: type as ChatContextType, id: id.toLowerCase() };
}

/** The assistant page with this object as the subject of the next message. */
export function chatHref(ref: ChatContextRef): string {
  return `/chat?about=${ref.type}:${encodeURIComponent(ref.id)}`;
}

export function contextLabel(type: ChatContextType): string {
  return NAMES[type].label;
}

/** The link shown under a message sent about this object. */
export function linkedLabel(type: ChatContextType): string {
  return NAMES[type].linked;
}

/** Shown in the chip when the object cannot be read (deleted, another space…). */
export function missingLabel(type: ChatContextType): string {
  return NAMES[type].missing;
}

/** Where the object lives in the app (tasks and journal entries have no detail page). */
export function objectHref(ref: ChatContextRef): string {
  const id = encodeURIComponent(ref.id);
  switch (ref.type) {
    case "goal":
      return `/goals?id=${id}`;
    case "review":
      return `/reviews?review=${id}`;
    case "journal":
      return "/journal";
    default:
      return "/tasks";
  }
}

/** What the chip names: a title, or the date of an entry, or the period of a review. */
export interface ContextObject {
  space_id: string;
  title?: string;
  entry_date?: string;
  period?: string;
  period_start?: string;
  period_end?: string;
}

export function objectTitle(type: ChatContextType, object: ContextObject): string {
  if (type === "journal") return formatDate(object.entry_date);
  if (type === "review") return periodLabel(object.period ?? "", object.period_start ?? "", object.period_end ?? "");
  return object.title ?? "";
}

/** The resource an agent must read in the space to receive this kind of object. */
export function contextResource(type: ChatContextType): ContextResource {
  return NAMES[type].resource;
}

/** The kind of object, in the plural (« L'agent … ne lit pas les bilans »). */
export function resourceLabel(type: ChatContextType): string {
  return NAMES[type].plural;
}

/** Why the chosen agent cannot receive the object (null: it can, or the general assistant).
 * Mirrors the server's check (403 context_not_allowed), so the owner is told before sending. */
export function agentContextIssue(
  agent: AgentInfo | undefined,
  type: ChatContextType,
): "not_a_resource" | "not_granted" | null {
  if (!agent) return null;
  const resource = contextResource(type);
  if (!agent.resources.includes(resource)) return "not_a_resource";
  if (agent.missing_permissions.includes(resource)) return "not_granted";
  return null;
}
