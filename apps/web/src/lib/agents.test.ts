import { describe, expect, it } from "vitest";

import {
  CORE_LABEL,
  NOTHING_DURABLE,
  agentLabel,
  agentOptions,
  consolidationSummary,
  instantMicros,
  isAgentChoice,
  joinFr,
  missingPermissions,
  newMessagesLabel,
  newOwnerMessages,
  plural,
  resourceLabel,
  resourceList,
  suggestedAgent,
  suggestionText,
} from "./agents";
import type { AgentInfo, Message, SendResult } from "./types";

const planning: AgentInfo = {
  name: "planning",
  label: "Planif. (serveur)",
  description: "Priorise les tâches.",
  resources: ["tasks", "goals"],
  missing_permissions: ["goals"],
};

function message(overrides: Partial<Message>): Message {
  return {
    id: "m",
    conversation_id: "c",
    role: "user",
    content: "Bonjour",
    status: "ok",
    model: null,
    agent: null,
    purge_after: null,
    retained: true,
    created_at: "2026-10-11T08:00:00Z",
    ...overrides,
  };
}

function result(overrides: Partial<SendResult>): SendResult {
  return {
    user_message: message({}),
    assistant_message: null,
    error: null,
    context_items: 0,
    agent: "core",
    suggested_agent: null,
    ...overrides,
  };
}

describe("agentLabel", () => {
  it("names the general assistant", () => {
    expect(agentLabel("core")).toBe(CORE_LABEL);
    expect(agentLabel(null)).toBe("Assistant général");
    expect(agentLabel(undefined)).toBe("Assistant général");
  });

  it("prefers the server label, then a local fallback", () => {
    expect(agentLabel("planning", [planning])).toBe("Planif. (serveur)");
    expect(agentLabel("planning")).toBe("Planification");
    expect(agentLabel("coaching", [planning])).toBe("Coaching");
    expect(agentLabel("memory")).toBe("Mémoire");
  });

  it("humanises an unknown agent name", () => {
    expect(agentLabel("finance_perso")).toBe("Finance perso");
  });
});

describe("isAgentChoice", () => {
  it("accepts only the agents the backend knows", () => {
    expect(isAgentChoice("core")).toBe(true);
    expect(isAgentChoice("memory")).toBe(true);
    expect(isAgentChoice("finance")).toBe(false);
    expect(isAgentChoice("")).toBe(false);
  });
});

describe("agentOptions", () => {
  it("puts the general assistant first, then the server agents", () => {
    const options = agentOptions([planning], "core");
    expect(options.map((o) => o.value)).toEqual(["core", "planning"]);
    expect(options[1]).toMatchObject({ label: "Planif. (serveur)", missing: ["goals"] });
  });

  it("keeps the current choice visible before the list is loaded", () => {
    expect(agentOptions(undefined, "coaching").map((o) => [o.value, o.label])).toEqual([
      ["core", "Assistant général"],
      ["coaching", "Coaching"],
    ]);
  });
});

describe("resource labels", () => {
  it("translates every agent resource", () => {
    expect(["tasks", "journal", "facts", "memories", "goals", "messages", "reviews"].map(resourceLabel)).toEqual([
      "tâches",
      "journal",
      "faits",
      "souvenirs",
      "objectifs",
      "messages",
      "bilans",
    ]);
    expect(resourceLabel("bank_accounts")).toBe("bank accounts");
  });

  it("joins lists the French way", () => {
    expect(joinFr([])).toBe("");
    expect(joinFr(["a"])).toBe("a");
    expect(joinFr(["a", "b"])).toBe("a et b");
    expect(joinFr(["a", "b", "c"])).toBe("a, b et c");
    expect(resourceList(["goals", "journal", "reviews"])).toBe("objectifs, journal et bilans");
  });
});

describe("send results", () => {
  it("extracts the resources an agent still needs", () => {
    const refused = result({
      agent: "coaching",
      error: { code: "agent_permission_required", title: "t", detail: null, missing: ["journal", "reviews"] },
    });
    expect(missingPermissions(refused)).toEqual(["journal", "reviews"]);
    expect(
      missingPermissions(result({ error: { code: "agent_permission_required", title: "t", detail: null } })),
    ).toEqual([]);
    expect(missingPermissions(result({ error: { code: "llm_unavailable", title: "t", detail: null } }))).toBeNull();
    expect(missingPermissions(result({}))).toBeNull();
  });

  it("suggests another agent only", () => {
    expect(suggestedAgent(result({ suggested_agent: "planning" }))).toBe("planning");
    expect(suggestedAgent(result({ agent: "planning", suggested_agent: "planning" }))).toBeNull();
    expect(suggestedAgent(result({}))).toBeNull();
    expect(suggestionText("Planification")).toBe(
      "L'agent Planification pourrait vous aider : l'utiliser pour le prochain message",
    );
  });
});

describe("consolidation", () => {
  it("pluralises the French way", () => {
    expect(plural(0, "message")).toBe("0 message");
    expect(plural(1, "message")).toBe("1 message");
    expect(plural(2, "message")).toBe("2 messages");
    expect(newMessagesLabel(0)).toBe("Aucun nouveau message à analyser");
    expect(newMessagesLabel(1)).toBe("1 nouveau message à analyser");
    expect(newMessagesLabel(4)).toBe("4 nouveaux messages à analyser");
  });

  it("compares server instants to the microsecond", () => {
    expect(instantMicros("2026-10-11T08:00:00.000001Z") - instantMicros("2026-10-11T08:00:00Z")).toBe(1);
    expect(instantMicros("2026-10-11T08:00:00.5Z") - instantMicros("2026-10-11T08:00:00Z")).toBe(500_000);
    expect(instantMicros("2026-10-11T10:00:00+02:00")).toBe(instantMicros("2026-10-11T08:00:00Z"));
    expect(Number.isNaN(instantMicros("pas une date"))).toBe(true);
  });

  it("counts only the owner's messages after the consolidation point", () => {
    const messages = [
      message({ id: "1", created_at: "2026-10-11T08:00:00.123456Z" }),
      message({ id: "2", role: "assistant", created_at: "2026-10-11T08:00:05Z" }),
      message({ id: "3", created_at: "2026-10-11T08:00:00.123457Z" }),
      message({ id: "4", created_at: "2026-10-11T09:00:00Z" }),
    ];
    expect(newOwnerMessages(messages, null)).toBe(3);
    expect(newOwnerMessages(messages, "2026-10-11T08:00:00.123456Z")).toBe(2);
    expect(newOwnerMessages(messages, "2026-10-11T09:00:00Z")).toBe(0);
  });

  it("summarises the outcome", () => {
    expect(consolidationSummary({ approval_id: null, messages: 3, proposed: 0 })).toBe(NOTHING_DURABLE);
    expect(consolidationSummary({ approval_id: "a1", messages: 1, proposed: 1 })).toBe(
      "1 proposition tirée de 1 message. Rien n'est enregistré avant votre approbation.",
    );
    expect(consolidationSummary({ approval_id: "a1", messages: 5, proposed: 3 })).toBe(
      "3 propositions tirées de 5 messages. Rien n'est enregistré avant votre approbation.",
    );
  });
});
