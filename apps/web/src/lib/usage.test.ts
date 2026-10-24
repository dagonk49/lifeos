import { describe, expect, it } from "vitest";

import type { AgentInfo, AgentPermission } from "./types";
import {
  ACCESS_STATUS_LABELS,
  WORDS_ONLY_NOTICE,
  accessExpiryLabel,
  accessStillOpen,
  agentAccessExpiry,
  agentAccessStatus,
  agentResourcesLabel,
  assistantQuotaSummary,
  assistantState,
  billingLabel,
  costBudgets,
  errorCodeLabel,
  formatCount,
  formatRatio,
  formatUsd,
  inFlightLabel,
  indexInProgress,
  indexProgressLabel,
  marginNote,
  matchBadge,
  modelLabel,
  outcomeInfo,
  parseUsd,
  purposeLabel,
  quotaResetLabel,
  redactionsLabel,
  searchHitHref,
  searchHitLabel,
  tierLabel,
  tierQuotas,
  tokensLabel,
  usageLevel,
  usagePercent,
} from "./usage";

/** French formatting uses (narrow) no-break spaces: compare with plain spaces. */
function plain(text: string): string {
  return text.replace(/[  ]/g, " ");
}

function permission(overrides: Partial<AgentPermission>): AgentPermission {
  return {
    id: "p",
    agent: "planning",
    space_id: "s1",
    resource: "tasks",
    access: "read",
    purpose: "Assistant Planification",
    expires_at: "2027-01-09T10:00:00Z",
    revoked_at: null,
    created_at: "2026-10-11T10:00:00Z",
    ...overrides,
  };
}

const NOW = new Date("2026-10-11T10:00:00Z");

describe("usagePercent", () => {
  it("rounds down so that 100 % means the limit is reached", () => {
    expect(usagePercent(0, 400)).toBe(0);
    expect(usagePercent(100, 400)).toBe(25);
    expect(usagePercent(399, 400)).toBe(99);
    expect(usagePercent(400, 400)).toBe(100);
    expect(usagePercent(500, 400)).toBe(100);
  });

  it("treats a zero or invalid limit as exhausted and bad usage as zero", () => {
    expect(usagePercent(0, 0)).toBe(100);
    expect(usagePercent(3, Number.NaN)).toBe(100);
    expect(usagePercent(Number.NaN, 10)).toBe(0);
    expect(usagePercent(-5, 10)).toBe(0);
  });
});

describe("usageLevel", () => {
  it("warns from 80 % and flags the reached limit", () => {
    expect(usageLevel(79, 100)).toBe("ok");
    expect(usageLevel(80, 100)).toBe("warning");
    expect(usageLevel(399, 400)).toBe("warning");
    expect(usageLevel(400, 400)).toBe("exceeded");
    expect(usageLevel(0, 0)).toBe("exceeded");
  });
});

describe("amounts and counts", () => {
  it("parses decimal strings", () => {
    expect(parseUsd("0.0123")).toBeCloseTo(0.0123);
    expect(parseUsd(2)).toBe(2);
    expect(parseUsd("abc")).toBe(0);
    expect(parseUsd(null)).toBe(0);
  });

  it("formats dollars in French, with more decimals under one cent", () => {
    expect(plain(formatUsd("1.5"))).toBe("1,50 $US");
    expect(plain(formatUsd("0.0042"))).toBe("0,0042 $US");
    expect(plain(formatUsd("0"))).toBe("0,00 $US");
  });

  it("groups digits", () => {
    expect(plain(formatCount(12345))).toBe("12 345");
    expect(plain(formatRatio(3, 1500))).toBe("3 / 1 500");
    expect(plain(tokensLabel(1234, 56))).toBe("1 234 / 56");
  });
});

describe("tiers", () => {
  it("labels tiers in French", () => {
    expect(tierLabel("fast")).toBe("Rapide");
    expect(tierLabel("deep")).toBe("Approfondi");
    expect(tierLabel("other")).toBe("other");
  });

  it("lists the daily quotas of both tiers, missing values as zero", () => {
    const quotas = tierQuotas({ fast: 320 }, { fast: 400, deep: 15 });
    expect(quotas).toEqual([
      { tier: "fast", label: "Rapide", used: 320, limit: 400, percent: 80, level: "warning", inFlight: 0 },
      { tier: "deep", label: "Approfondi", used: 0, limit: 15, percent: 0, level: "ok", inFlight: 0 },
    ]);
  });

  it("keeps the calls in flight of each tier, already counted in the day's requests", () => {
    const quotas = tierQuotas({ fast: 3, deep: 1 }, { fast: 398, deep: 13 }, { fast: 2 });
    expect(quotas.map((q) => [q.tier, q.used, q.inFlight])).toEqual([
      ["fast", 3, 2],
      ["deep", 1, 0],
    ]);
    expect(inFlightLabel(0)).toBe("");
    expect(inFlightLabel(1)).toBe("dont 1 appel en cours");
    expect(inFlightLabel(2)).toBe("dont 2 appels en cours");
    expect(inFlightLabel(Number.NaN)).toBe("");
  });

  it("explains the safety margins under the configured caps and budgets", () => {
    const free = { billing: "free" as const, daily_request_margin: 2, budget_margin_ratio: 0.05 };
    expect(marginNote(free)).toBe("Marge de sécurité : 2 requêtes gardées sous chaque plafond configuré.");
    expect(marginNote({ ...free, daily_request_margin: 1 })).toBe(
      "Marge de sécurité : 1 requête gardée sous chaque plafond configuré.",
    );
    expect(marginNote({ ...free, daily_request_margin: 0 })).toBe("");
    expect(marginNote({ ...free, billing: "paid" })).toBe(
      "Marge de sécurité : 5 % des budgets sont gardés en réserve.",
    );
    expect(marginNote({ ...free, billing: "paid", budget_margin_ratio: 0 })).toBe("");
  });

  it("names unconfigured models", () => {
    expect(modelLabel("gemini-3-flash")).toBe("gemini-3-flash");
    expect(modelLabel(null)).toBe("Non configuré");
    expect(modelLabel("  ")).toBe("Non configuré");
  });
});

describe("costBudgets", () => {
  it("compares the costs of the day and month with the USD budgets", () => {
    const [day, month] = costBudgets({
      cost_today_usd: "0.5",
      cost_month_usd: "4.00",
      daily_budget_usd: 1,
      monthly_budget_usd: 5,
    });
    expect(day).toMatchObject({ key: "day", label: "Coût du jour", percent: 50, level: "ok" });
    expect(plain(day?.text ?? "")).toBe("0,50 $US / 1,00 $US");
    expect(month).toMatchObject({ key: "month", label: "Coût du mois", percent: 80, level: "warning" });
  });
});

describe("quotaResetLabel", () => {
  const now = new Date(2026, 9, 11, 7, 30);

  it("says when the quotas start again, in local time", () => {
    expect(quotaResetLabel(new Date(2026, 9, 11, 9, 0).toISOString(), now)).toBe("Renouvellement à 09:00");
    expect(quotaResetLabel(new Date(2026, 9, 12, 9, 0).toISOString(), now)).toBe("Renouvellement demain à 09:00");
    expect(quotaResetLabel(new Date(2026, 9, 14, 9, 0).toISOString(), now)).toMatch(
      /^Renouvellement le 14 oct\. 2026 à 09:00$/,
    );
  });

  it("returns nothing for a missing or invalid date", () => {
    expect(quotaResetLabel(null, now)).toBe("");
    expect(quotaResetLabel("not a date", now)).toBe("");
  });
});

describe("assistant state and billing", () => {
  it("lets the emergency stop win", () => {
    expect(assistantState({ enabled: true, paused: true })).toBe("paused");
    expect(assistantState({ enabled: false, paused: true })).toBe("paused");
    expect(assistantState({ enabled: false, paused: false })).toBe("disabled");
    expect(assistantState({ enabled: true, paused: false })).toBe("active");
  });

  it("labels the billing mode", () => {
    expect(billingLabel("free")).toMatch(/^Gratuite/);
    expect(billingLabel("paid")).toMatch(/^Payante/);
  });

  it("summarises the quotas for the dashboard", () => {
    const free = assistantQuotaSummary({
      billing: "free",
      requests_today: { fast: 3, deep: 0 },
      daily_request_caps: { fast: 400, deep: 15 },
      cost_month_usd: "0",
    });
    expect(free).toBe("Requêtes du jour : rapide 3 / 400 · approfondi 0 / 15");
    const paid = assistantQuotaSummary({
      billing: "paid",
      requests_today: { fast: 3, deep: 0 },
      daily_request_caps: { fast: 400, deep: 15 },
      cost_month_usd: "2.5",
    });
    expect(plain(paid)).toBe("Coût du mois : 2,50 $US");
  });
});

describe("recent calls", () => {
  it("labels purposes, including agents", () => {
    expect(purposeLabel("chat.reply")).toBe("Assistant");
    expect(purposeLabel("review.weekly")).toBe("Bilan hebdomadaire");
    expect(purposeLabel("agent.planning")).toBe("Agent Planification");
    expect(purposeLabel("something.else")).toBe("something.else");
  });

  it("labels outcomes with a tone", () => {
    expect(outcomeInfo("ok")).toEqual({ label: "Réussi", tone: "teal" });
    expect(outcomeInfo("budget_exceeded")).toEqual({ label: "Limite atteinte", tone: "amber" });
    expect(outcomeInfo("error")).toEqual({ label: "Échec", tone: "red" });
    expect(outcomeInfo("reserved")).toEqual({ label: "En cours", tone: "blue" });
    expect(outcomeInfo("abandoned")).toEqual({ label: "Abandonné", tone: "neutral" });
    expect(outcomeInfo("weird")).toEqual({ label: "weird", tone: "neutral" });
  });

  it("explains error codes and masked items", () => {
    expect(errorCodeLabel("llm_paused")).toMatch(/en pause/);
    expect(errorCodeLabel("unknown_code")).toBe("unknown_code");
    expect(errorCodeLabel(null)).toBe("");
    expect(redactionsLabel(0)).toBe("Aucun");
    expect(redactionsLabel(1)).toBe("1 élément");
    expect(redactionsLabel(3)).toBe("3 éléments");
  });
});

describe("agent access", () => {
  const agent: AgentInfo = {
    name: "planning",
    label: "Planification",
    description: "",
    resources: ["tasks", "goals"],
    missing_permissions: [],
  };

  it("derives the access status", () => {
    expect(agentAccessStatus(agent)).toBe("granted");
    expect(agentAccessStatus({ ...agent, missing_permissions: ["goals"] })).toBe("partial");
    expect(agentAccessStatus({ ...agent, missing_permissions: ["tasks", "goals"] })).toBe("none");
    expect(ACCESS_STATUS_LABELS.granted).toBe("Accès accordé");
  });

  it("describes the read-only resources", () => {
    expect(agentResourcesLabel(["tasks", "goals"])).toBe("Lecture seule : tâches et objectifs");
    expect(agentResourcesLabel([])).toBe("Lecture seule");
  });

  it("finds when the access ends: the earliest of the per-resource latest expiries", () => {
    const permissions = [
      permission({ resource: "tasks", expires_at: "2027-01-09T10:00:00Z" }),
      permission({ resource: "tasks", expires_at: "2026-12-01T10:00:00Z" }),
      permission({ resource: "goals", expires_at: "2026-12-15T10:00:00Z" }),
    ];
    expect(agentAccessExpiry(permissions, "planning", "s1", ["tasks", "goals"], NOW)).toBe("2026-12-15T10:00:00Z");
  });

  it("ignores revoked, expired and foreign permissions", () => {
    const permissions = [
      permission({ resource: "tasks" }),
      permission({ resource: "goals", revoked_at: "2026-10-10T10:00:00Z" }),
      permission({ resource: "goals", expires_at: "2026-10-01T10:00:00Z" }),
      permission({ resource: "goals", space_id: "s2" }),
      permission({ resource: "goals", agent: "coaching" }),
    ];
    expect(agentAccessExpiry(permissions, "planning", "s1", ["tasks", "goals"], NOW)).toBeUndefined();
    expect(agentAccessExpiry(permissions, "planning", "s1", [], NOW)).toBeUndefined();
  });

  it("reports a never-expiring access as null", () => {
    const permissions = [permission({ resource: "tasks", expires_at: null })];
    expect(agentAccessExpiry(permissions, "planning", "s1", ["tasks"], NOW)).toBeNull();
  });

  it("labels the expiry", () => {
    expect(accessExpiryLabel(undefined)).toBe("");
    expect(accessExpiryLabel(null)).toBe("sans limite de durée");
    expect(accessExpiryLabel("2027-01-09T10:00:00Z")).toMatch(/^jusqu'au 9 janv\. 2027$/);
  });

  it("detects an access kept open by other permissions after a withdrawal", () => {
    expect(accessStillOpen([agent], "planning")).toBe(true);
    expect(accessStillOpen([{ ...agent, missing_permissions: ["tasks", "goals"] }], "planning")).toBe(false);
    expect(accessStillOpen([agent], "coaching")).toBe(false);
    expect(accessStillOpen(undefined, "planning")).toBe(false);
  });
});

describe("search", () => {
  it("badges semantic matches only", () => {
    expect(matchBadge("semantic")?.label).toBe("Sens proche");
    expect(matchBadge("both")?.label).toBe("Mots + sens");
    expect(matchBadge("text")).toBeNull();
    expect(matchBadge(undefined)).toBeNull();
  });

  it("describes the index progress", () => {
    expect(plain(indexProgressLabel({ indexed: 1200, total: 1500 }))).toBe("Index sémantique : 1 200 / 1 500 éléments");
    expect(indexProgressLabel({ indexed: 1, total: 1 })).toBe("Index sémantique : 1 / 1 élément");
    expect(indexInProgress({ semantic: true, indexed: 3, total: 5 })).toBe(true);
    expect(indexInProgress({ semantic: true, indexed: 5, total: 5 })).toBe(false);
    expect(indexInProgress({ semantic: false, indexed: 0, total: 5 })).toBe(false);
    expect(indexInProgress(undefined)).toBe(false);
    expect(WORDS_ONLY_NOTICE).toBe("Recherche par mots uniquement : modèle local indisponible");
  });

  it("labels hit types, goals included", () => {
    expect(searchHitLabel("goal")).toBe("Objectif");
    expect(searchHitLabel("fact")).toBe("Fait");
    expect(searchHitLabel("other")).toBe("other");
  });

  it("links goals to their detail", () => {
    expect(searchHitHref({ type: "goal", id: "a b" })).toBe("/goals?id=a%20b");
    expect(searchHitHref({ type: "fact", id: "x" })).toBe("/memory");
    expect(searchHitHref({ type: "message", id: "x" })).toBe("/chat");
  });
});
