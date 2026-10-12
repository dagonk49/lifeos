// Pure helpers for the AI usage panel (quotas, costs, emergency stop), the agent access
// settings and the search page (semantic match badges, index progress).
// No React here so that everything is unit-testable.

import { agentLabel, resourceList } from "./agents";
import { problemMessage } from "./api";
import { formatDate, formatTime } from "./format";
import type { AgentInfo, AgentPermission, Dashboard, IndexStatus, LLMTier, LLMUsage, SearchHit } from "./types";

/** Query key of GET /v1/guardian/llm-usage. */
export const USAGE_KEY = ["llm-usage"] as const;

/** Query key of GET /v1/search/status. */
export const INDEX_STATUS_KEY = ["search-status"] as const;

// --- Tiers, quotas and amounts ---------------------------------------------------------

export const LLM_TIERS: readonly LLMTier[] = ["fast", "deep"];

const TIER_LABELS: Record<LLMTier, string> = { fast: "Rapide", deep: "Approfondi" };

const TIER_DESCRIPTIONS: Record<LLMTier, string> = {
  fast: "Assistant, agents, analyse du journal, bilans quotidiens",
  deep: "Bilans hebdomadaires et mensuels",
};

/** "Rapide" (fast model), "Approfondi" (deep model). */
export function tierLabel(tier: string): string {
  return TIER_LABELS[tier as LLMTier] ?? tier;
}

/** What each tier is used for. */
export function tierDescription(tier: string): string {
  return TIER_DESCRIPTIONS[tier as LLMTier] ?? "";
}

/** Model name of a tier, or a French placeholder when the server has none. */
export function modelLabel(model: string | null | undefined): string {
  return model?.trim() ? model : "Non configuré";
}

function finite(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

/**
 * Share of a limit already used, as an integer between 0 and 100. Rounded down so that
 * 100 % only shows once the limit is really reached; a zero limit allows nothing (the
 * backend refuses as soon as `used >= limit`), so it reads as 100 %.
 */
export function usagePercent(used: number, limit: number): number {
  const value = finite(used);
  if (!Number.isFinite(limit) || limit <= 0 || value >= limit) return 100;
  return Math.min(99, Math.floor((100 * value) / limit));
}

export type UsageLevel = "ok" | "warning" | "exceeded";

/** Colour level of a gauge: warning from 80 %, exceeded once the limit is reached. */
export function usageLevel(used: number, limit: number): UsageLevel {
  const value = finite(used);
  if (!Number.isFinite(limit) || limit <= 0 || value >= limit) return "exceeded";
  if (value >= 0.8 * limit) return "warning";
  return "ok";
}

/** Amounts arrive as decimal strings ("0.0123"); anything unreadable counts as 0. */
export function parseUsd(value: string | number | null | undefined): number {
  const amount = typeof value === "number" ? value : Number.parseFloat(value ?? "");
  return Number.isFinite(amount) ? amount : 0;
}

/** "0,12 $US"; amounts under one cent keep up to four decimals ("0,0042 $US"). */
export function formatUsd(value: string | number | null | undefined): string {
  const amount = parseUsd(value);
  const small = amount !== 0 && Math.abs(amount) < 0.01;
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: small ? 4 : 2,
  }).format(amount);
}

const integerFormat = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });

/** "12 345" (French digit grouping). */
export function formatCount(value: number): string {
  return integerFormat.format(Number.isFinite(value) ? value : 0);
}

/** "12 / 400". */
export function formatRatio(used: number, limit: number): string {
  return `${formatCount(used)} / ${formatCount(limit)}`;
}

export interface Gauge {
  used: number;
  limit: number;
  percent: number;
  level: UsageLevel;
}

export function gauge(used: number, limit: number): Gauge {
  return { used, limit, percent: usagePercent(used, limit), level: usageLevel(used, limit) };
}

export interface TierQuota extends Gauge {
  tier: LLMTier;
  label: string;
}

/** Requests of the day against the local daily caps, one entry per tier. */
export function tierQuotas(
  requests: Partial<Record<string, number>>,
  caps: Partial<Record<string, number>>,
): TierQuota[] {
  return LLM_TIERS.map((tier) => ({ tier, label: tierLabel(tier), ...gauge(requests[tier] ?? 0, caps[tier] ?? 0) }));
}

export interface CostBudget extends Gauge {
  key: "day" | "month";
  label: string;
  /** "0,42 $US / 1,00 $US". */
  text: string;
}

/** Paid billing: estimated cost of the day and of the month against the USD budgets. */
export function costBudgets(
  usage: Pick<LLMUsage, "cost_today_usd" | "cost_month_usd" | "daily_budget_usd" | "monthly_budget_usd">,
): CostBudget[] {
  const rows: [CostBudget["key"], string, string, number][] = [
    ["day", "Coût du jour", usage.cost_today_usd, usage.daily_budget_usd],
    ["month", "Coût du mois", usage.cost_month_usd, usage.monthly_budget_usd],
  ];
  return rows.map(([key, label, cost, budget]) => ({
    key,
    label,
    text: `${formatUsd(cost)} / ${formatUsd(budget)}`,
    ...gauge(parseUsd(cost), budget),
  }));
}

function startOfLocalDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * When the daily quotas start again, in local time: "Renouvellement à 09:00", or
 * "Renouvellement demain à 09:00" when the reset falls on the next local day.
 */
export function quotaResetLabel(resetsAt: string | null | undefined, now: Date = new Date()): string {
  if (!resetsAt) return "";
  const reset = new Date(resetsAt);
  if (Number.isNaN(reset.getTime())) return "";
  const time = formatTime(resetsAt);
  const days = Math.round((startOfLocalDay(reset) - startOfLocalDay(now)) / 86_400_000);
  if (days <= 0) return `Renouvellement à ${time}`;
  if (days === 1) return `Renouvellement demain à ${time}`;
  return `Renouvellement le ${formatDate(resetsAt)} à ${time}`;
}

// --- Assistant state and billing -------------------------------------------------------

export type AssistantState = "paused" | "disabled" | "active";

/** The emergency stop wins over everything; "disabled": no provider on the server. */
export function assistantState(assistant: { enabled: boolean; paused: boolean }): AssistantState {
  if (assistant.paused) return "paused";
  return assistant.enabled ? "active" : "disabled";
}

export const ASSISTANT_STATES: Record<AssistantState, { label: string; tone: "amber" | "neutral" | "teal" }> = {
  paused: { label: "En pause", tone: "amber" },
  disabled: { label: "Désactivé", tone: "neutral" },
  active: { label: "Actif", tone: "teal" },
};

export const PAUSE_EXPLANATION =
  "Arrêt d'urgence : tant que la pause est active, aucun appel n'est envoyé au fournisseur IA cloud. Tâches, journal, mémoire, recherche et tableau de bord restent disponibles.";

export const FREE_BILLING_NOTE =
  "Projet sans compte de facturation : Google ne peut rien facturer ; au-delà du quota gratuit, les requêtes sont refusées.";

export const PAID_BILLING_NOTE =
  "Crédit prépayé : chaque appel est facturé par Google. Les coûts affichés sont estimés localement ; au-delà des budgets, LifeOS refuse les appels.";

export function billingLabel(billing: string): string {
  return billing === "paid" ? "Payante (crédit prépayé)" : "Gratuite (sans compte de facturation)";
}

/** One line for the dashboard: requests of the day (free) or cost of the month (paid). */
export function assistantQuotaSummary(
  assistant: Pick<Dashboard["assistant"], "billing" | "requests_today" | "daily_request_caps" | "cost_month_usd">,
): string {
  if (assistant.billing === "paid") return `Coût du mois : ${formatUsd(assistant.cost_month_usd)}`;
  const parts = tierQuotas(assistant.requests_today, assistant.daily_request_caps).map(
    (q) => `${q.label.toLowerCase()} ${formatRatio(q.used, q.limit)}`,
  );
  return `Requêtes du jour : ${parts.join(" · ")}`;
}

// --- Recent calls ----------------------------------------------------------------------

const PURPOSE_LABELS: Record<string, string> = {
  "chat.reply": "Assistant",
  "chat.consolidate": "Consolidation d'une conversation",
  "journal.analyze": "Analyse du journal",
  "review.daily": "Bilan quotidien",
  "review.weekly": "Bilan hebdomadaire",
  "review.monthly": "Bilan mensuel",
};

/** French label of an LLM call purpose ("agent.planning" -> "Agent Planification"). */
export function purposeLabel(purpose: string): string {
  const known = PURPOSE_LABELS[purpose];
  if (known) return known;
  if (purpose.startsWith("agent.")) return `Agent ${agentLabel(purpose.slice("agent.".length))}`;
  return purpose;
}

export type OutcomeTone = "teal" | "amber" | "red" | "neutral";

const OUTCOMES: Record<string, { label: string; tone: OutcomeTone }> = {
  ok: { label: "Réussi", tone: "teal" },
  denied: { label: "Refusé", tone: "amber" },
  budget_exceeded: { label: "Limite atteinte", tone: "amber" },
  error: { label: "Échec", tone: "red" },
};

export function outcomeInfo(outcome: string): { label: string; tone: OutcomeTone } {
  return OUTCOMES[outcome] ?? { label: outcome, tone: "neutral" };
}

/** French explanation of an error code (the code itself when unknown). */
export function errorCodeLabel(code: string | null | undefined): string {
  return code ? problemMessage(code, code) : "";
}

/** Input / output tokens: "1 234 / 56". */
export function tokensLabel(input: number, output: number): string {
  return `${formatCount(input)} / ${formatCount(output)}`;
}

/** Masked sensitive items before sending: "Aucun", "1 élément", "3 éléments". */
export function redactionsLabel(count: number): string {
  if (!count) return "Aucun";
  return count >= 2 ? `${formatCount(count)} éléments` : "1 élément";
}

// --- Specialised agents: access per space ----------------------------------------------

export type AgentAccessStatus = "granted" | "partial" | "none";

/** granted: reads all its resources in the space; partial: some; none: nothing yet. */
export function agentAccessStatus(agent: Pick<AgentInfo, "resources" | "missing_permissions">): AgentAccessStatus {
  if (agent.missing_permissions.length === 0) return "granted";
  const missing = new Set(agent.missing_permissions);
  return agent.resources.some((r) => !missing.has(r)) ? "partial" : "none";
}

export const ACCESS_STATUS_LABELS: Record<AgentAccessStatus, string> = {
  granted: "Accès accordé",
  partial: "Accès partiel",
  none: "Accès non accordé",
};

export const ACCESS_STATUS_TONES: Record<AgentAccessStatus, "teal" | "amber" | "neutral"> = {
  granted: "teal",
  partial: "amber",
  none: "neutral",
};

/** "Lecture seule : tâches et objectifs". */
export function agentResourcesLabel(resources: readonly string[]): string {
  return resources.length ? `Lecture seule : ${resourceList(resources)}` : "Lecture seule";
}

/**
 * End of an agent's read access to all its resources in a space, from the active
 * permissions: the earliest of the per-resource latest expiries. Returns `null` when the
 * access never expires and `undefined` when some resource is not covered at all.
 */
export function agentAccessExpiry(
  permissions: readonly AgentPermission[],
  agent: string,
  spaceId: string,
  resources: readonly string[],
  now: Date = new Date(),
): string | null | undefined {
  if (resources.length === 0) return undefined;
  let end: number | undefined;
  let endIso: string | null = null;
  for (const resource of resources) {
    let latest: number | undefined;
    let latestIso: string | null = null;
    for (const p of permissions) {
      if (p.agent !== agent || p.space_id !== spaceId || p.resource !== resource || p.revoked_at) continue;
      const expires = p.expires_at === null ? Number.POSITIVE_INFINITY : Date.parse(p.expires_at);
      if (Number.isNaN(expires) || expires <= now.getTime()) continue;
      if (latest === undefined || expires > latest) {
        latest = expires;
        latestIso = p.expires_at;
      }
    }
    if (latest === undefined) return undefined;
    if (end === undefined || latest < end) {
      end = latest;
      endIso = latestIso;
    }
  }
  return endIso;
}

/** "jusqu'au 9 janv. 2027", "sans limite de durée", or "" when the access is incomplete. */
export function accessExpiryLabel(expiry: string | null | undefined): string {
  if (expiry === undefined) return "";
  if (expiry === null) return "sans limite de durée";
  return `jusqu'au ${formatDate(expiry)}`;
}

/**
 * After withdrawing an agent's access, other permissions (granted by hand or by the
 * automatic reviews) may still let it read some resources: the switch stays on.
 */
export function accessStillOpen(agents: readonly AgentInfo[] | undefined, name: string): boolean {
  const agent = agents?.find((a) => a.name === name);
  return agent !== undefined && agentAccessStatus(agent) !== "none";
}

// --- Search: semantic match and index progress -----------------------------------------

export interface MatchBadge {
  label: string;
  /** Explanation for the tooltip and screen readers. */
  description: string;
  tone: "violet" | "blue";
}

/** How a hit was found; plain word matches ("text") get no badge. */
export function matchBadge(match: SearchHit["match"] | undefined): MatchBadge | null {
  if (match === "semantic") {
    return { label: "Sens proche", description: "Trouvé par proximité de sens, sans les mots exacts", tone: "violet" };
  }
  if (match === "both") {
    return { label: "Mots + sens", description: "Trouvé par les mots et par le sens", tone: "blue" };
  }
  return null;
}

export const WORDS_ONLY_NOTICE = "Recherche par mots uniquement : modèle local indisponible";

/** "Index sémantique : 120 / 150 éléments". */
export function indexProgressLabel(status: Pick<IndexStatus, "indexed" | "total">): string {
  const noun = status.total >= 2 ? "éléments" : "élément";
  return `Index sémantique : ${formatRatio(status.indexed, status.total)} ${noun}`;
}

/** Whether the local index still has items to embed. */
export function indexInProgress(status: Pick<IndexStatus, "semantic" | "indexed" | "total"> | undefined): boolean {
  return Boolean(status?.semantic) && (status?.indexed ?? 0) < (status?.total ?? 0);
}

const HIT_LABELS: Record<SearchHit["type"], string> = {
  task: "Tâche",
  journal: "Journal",
  fact: "Fait",
  memory: "Souvenir",
  message: "Message",
  goal: "Objectif",
  document: "Document",
};

export function searchHitLabel(type: string): string {
  return HIT_LABELS[type as SearchHit["type"]] ?? type;
}

const HIT_LINKS: Record<SearchHit["type"], string> = {
  task: "/tasks",
  journal: "/journal",
  fact: "/memory",
  memory: "/memory",
  message: "/chat",
  goal: "/goals",
  document: "/documents",
};

/** Where "Ouvrir" leads; goals open directly on their detail. */
export function searchHitHref(hit: Pick<SearchHit, "type" | "id">): string {
  if (hit.type === "goal") return `/goals?id=${encodeURIComponent(hit.id)}`;
  if (hit.type === "document") return `/documents?id=${encodeURIComponent(hit.id)}`;
  return HIT_LINKS[hit.type] ?? "/";
}
