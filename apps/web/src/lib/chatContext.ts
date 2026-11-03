// Pure helpers for « Discuter de… » (task 24): the task or goal the owner explicitly
// chooses as the subject of a message. Carried by the URL (/chat?about=goal:<uuid>) and
// sent as `context.ref`; the server checks it again (same space, rights) and sends it as
// data, never as instructions.

import type { AgentInfo, ChatContextRef, ChatContextType } from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TYPES: readonly string[] = ["task", "goal"];

/** `task:<uuid>` or `goal:<uuid>`; anything else (unknown type, not a UUID) is ignored. */
export function parseAbout(param: string | null | undefined): ChatContextRef | null {
  if (!param) return null;
  const separator = param.indexOf(":");
  if (separator < 0) return null;
  const type = param.slice(0, separator);
  const id = param.slice(separator + 1);
  if (!TYPES.includes(type) || !UUID.test(id)) return null;
  return { type: type as ChatContextType, id: id.toLowerCase() };
}

/** The assistant page with this object as the subject of the next message. */
export function chatHref(ref: ChatContextRef): string {
  return `/chat?about=${ref.type}:${encodeURIComponent(ref.id)}`;
}

export function contextLabel(type: ChatContextType): string {
  return type === "goal" ? "Objectif" : "Tâche";
}

/** The link shown under a message sent about this object. */
export function linkedLabel(type: ChatContextType): string {
  return type === "goal" ? "Objectif lié" : "Tâche liée";
}

/** Where the object lives in the app (tasks have no detail page). */
export function objectHref(ref: ChatContextRef): string {
  return ref.type === "goal" ? `/goals?id=${encodeURIComponent(ref.id)}` : "/tasks";
}

/** The resource an agent must read in the space to receive this kind of object. */
export function contextResource(type: ChatContextType): "tasks" | "goals" {
  return type === "goal" ? "goals" : "tasks";
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
