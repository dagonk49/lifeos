// Pure helpers for the assistant page: on-demand specialised agents (F08) and
// conversation consolidation (F07). No React here so that everything is unit-testable.

import type { AgentInfo, AgentName, ConsolidationResult, Message, SendResult } from "./types";

/** "core" is the general assistant; specialised agents are only used when chosen. */
export type AgentChoice = AgentName | "core";

export const CORE_AGENT: AgentChoice = "core";
export const CORE_LABEL = "Assistant général";
export const CORE_DESCRIPTION =
  "Répond avec le contexte utile de l'espace. Les agents spécialisés ne sont utilisés que si vous les choisissez.";

/** Days of read access granted to an agent from the assistant page (backend ACCESS_DAYS). */
export const AGENT_ACCESS_DAYS = 90;

/** Query key of GET /v1/agents?space_id=… */
export function agentsKey(spaceId: string | null) {
  return ["agents", spaceId] as const;
}

const AGENT_LABELS: Record<string, string> = {
  core: CORE_LABEL,
  planning: "Planification",
  coaching: "Coaching",
  memory: "Mémoire",
  finance: "Finances",
};

function humanize(value: string): string {
  const text = value.replace(/[_-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/** Label of an agent: the server's label when known, else a local fallback. */
export function agentLabel(name: string | null | undefined, agents?: readonly AgentInfo[]): string {
  if (!name || name === CORE_AGENT) return CORE_LABEL;
  return agents?.find((a) => a.name === name)?.label ?? AGENT_LABELS[name] ?? humanize(name);
}

export function isAgentChoice(value: string): value is AgentChoice {
  return (
    value === "core" ||
    value === "planning" ||
    value === "coaching" ||
    value === "memory" ||
    value === "finance"
  );
}

export interface AgentOption {
  value: AgentChoice;
  label: string;
  description: string;
  /** Resources the agent still cannot read in this space. */
  missing: string[];
}

/**
 * Options of the agent selector: the general assistant first, then the server's agents.
 * The current choice is always present, even before the list is loaded, so that the
 * select never shows another agent than the one that will be used.
 */
export function agentOptions(agents: readonly AgentInfo[] | undefined, selected: AgentChoice): AgentOption[] {
  const options: AgentOption[] = [{ value: CORE_AGENT, label: CORE_LABEL, description: CORE_DESCRIPTION, missing: [] }];
  for (const agent of agents ?? []) {
    options.push({
      value: agent.name,
      label: agent.label || agentLabel(agent.name),
      description: agent.description,
      missing: agent.missing_permissions,
    });
  }
  if (!options.some((o) => o.value === selected)) {
    options.push({ value: selected, label: agentLabel(selected), description: "", missing: [] });
  }
  return options;
}

const RESOURCE_LABELS: Record<string, string> = {
  tasks: "tâches",
  journal: "journal",
  facts: "faits",
  memories: "souvenirs",
  goals: "objectifs",
  messages: "messages",
  reviews: "bilans",
  documents: "documents",
  finances: "finances",
};

/** Lower-case French name of a data resource, for use inside a sentence. */
export function resourceLabel(resource: string): string {
  return RESOURCE_LABELS[resource] ?? resource.replace(/[_-]+/g, " ");
}

/** "a", "a et b", "a, b et c". */
export function joinFr(items: readonly string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} et ${items[items.length - 1]}`;
}

export function resourceList(resources: readonly string[]): string {
  return joinFr(resources.map(resourceLabel));
}

/** Resources to grant when the reply failed with `agent_permission_required`, else null. */
export function missingPermissions(result: SendResult): string[] | null {
  if (result.error?.code !== "agent_permission_required") return null;
  return result.error.missing ?? [];
}

/** Agent worth suggesting for the next message (never the one just used). */
export function suggestedAgent(result: SendResult): AgentName | null {
  const suggestion = result.suggested_agent;
  return suggestion && suggestion !== result.agent ? suggestion : null;
}

export function suggestionText(label: string): string {
  return `L'agent ${label} pourrait vous aider : l'utiliser pour le prochain message`;
}

// --- Consolidation (F07) -----------------------------------------------------------------

/** "1 message", "3 messages" (French: 0 and 1 are singular). */
export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${Math.abs(count) >= 2 ? pluralForm : singular}`;
}

/**
 * Microseconds since the epoch of a server instant. The backend keeps microseconds while
 * `Date` only keeps milliseconds: comparing with `Date` alone could treat a message written
 * just after the consolidation point as already consolidated.
 */
export function instantMicros(iso: string): number {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return Number.NaN;
  const fraction = /T\d{2}:\d{2}:\d{2}\.(\d+)/.exec(iso)?.[1] ?? "";
  return ms * 1000 + Number(fraction.padEnd(6, "0").slice(3, 6));
}

/** Owner messages written after the consolidation point: what the next run would analyse. */
export function newOwnerMessages(messages: readonly Message[], consolidatedUntil: string | null): number {
  const since = consolidatedUntil ? instantMicros(consolidatedUntil) : null;
  return messages.filter(
    (m) => m.role === "user" && (since === null || Number.isNaN(since) || instantMicros(m.created_at) > since),
  ).length;
}

export function newMessagesLabel(count: number): string {
  if (count === 0) return "Aucun nouveau message à analyser";
  return count === 1 ? "1 nouveau message à analyser" : `${count} nouveaux messages à analyser`;
}

export const NOTHING_DURABLE = "Rien de durable à retenir dans les nouveaux messages.";

export function consolidationSummary(result: ConsolidationResult): string {
  if (result.approval_id === null) return NOTHING_DURABLE;
  const proposals = plural(result.proposed, "proposition");
  const drawn = result.proposed >= 2 ? "tirées" : "tirée";
  return `${proposals} ${drawn} de ${plural(result.messages, "message")}. Rien n'est enregistré avant votre approbation.`;
}
