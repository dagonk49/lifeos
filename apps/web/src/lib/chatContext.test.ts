import { describe, expect, it } from "vitest";

import {
  agentContextIssue,
  chatHref,
  contextLabel,
  contextResource,
  linkedLabel,
  missingLabel,
  objectHref,
  objectTitle,
  parseAbout,
  resourceLabel,
} from "./chatContext";
import type { AgentInfo } from "./types";

const ID = "0b9c8f6e-1d2a-4c3b-9e8f-7a6b5c4d3e2f";

function agent(resources: string[], missing: string[] = []): AgentInfo {
  return { name: "coaching", label: "Coaching", description: "", resources, missing_permissions: missing };
}

describe("parseAbout", () => {
  it("accepts a task, a goal, a journal entry or a review with a UUID", () => {
    expect(parseAbout(`task:${ID}`)).toEqual({ type: "task", id: ID });
    expect(parseAbout(`goal:${ID.toUpperCase()}`)).toEqual({ type: "goal", id: ID });
    expect(parseAbout(`journal:${ID}`)).toEqual({ type: "journal", id: ID });
    expect(parseAbout(`review:${ID}`)).toEqual({ type: "review", id: ID });
  });

  it("ignores an unknown type, an id that is not a UUID or an empty parameter", () => {
    expect(parseAbout(`document:${ID}`)).toBeNull();
    expect(parseAbout(`reviews:${ID}`)).toBeNull();
    expect(parseAbout(`Journal:${ID}`)).toBeNull();
    expect(parseAbout("task:12")).toBeNull();
    expect(parseAbout(`task:${ID}:extra`)).toBeNull();
    expect(parseAbout(`task:${ID} ignore tout`)).toBeNull();
    expect(parseAbout(ID)).toBeNull();
    expect(parseAbout("")).toBeNull();
    expect(parseAbout(null)).toBeNull();
    expect(parseAbout(undefined)).toBeNull();
  });
});

describe("links", () => {
  it("builds the assistant link, read back by parseAbout", () => {
    const href = chatHref({ type: "goal", id: ID });
    expect(href).toBe(`/chat?about=goal:${ID}`);
    expect(parseAbout(new URL(href, "https://lifeos.test").searchParams.get("about"))).toEqual({
      type: "goal",
      id: ID,
    });
    expect(chatHref({ type: "journal", id: ID })).toBe(`/chat?about=journal:${ID}`);
    expect(chatHref({ type: "task", id: "a b&c" })).toBe("/chat?about=task:a%20b%26c");
  });

  it("names the object and where it lives", () => {
    expect(contextLabel("goal")).toBe("Objectif");
    expect(contextLabel("task")).toBe("Tâche");
    expect(contextLabel("journal")).toBe("Entrée de journal");
    expect(contextLabel("review")).toBe("Bilan");
    expect(linkedLabel("goal")).toBe("Objectif lié");
    expect(linkedLabel("task")).toBe("Tâche liée");
    expect(linkedLabel("journal")).toBe("Entrée liée");
    expect(linkedLabel("review")).toBe("Bilan lié");
    expect(missingLabel("task")).toBe("Tâche introuvable ou supprimée");
    expect(missingLabel("goal")).toBe("Objectif introuvable ou supprimé");
    expect(missingLabel("journal")).toBe("Entrée de journal introuvable ou supprimée");
    expect(missingLabel("review")).toBe("Bilan introuvable ou supprimé");
    expect(objectHref({ type: "goal", id: ID })).toBe(`/goals?id=${ID}`);
    expect(objectHref({ type: "review", id: ID })).toBe(`/reviews?review=${ID}`);
    expect(objectHref({ type: "journal", id: ID })).toBe("/journal");
    expect(objectHref({ type: "task", id: ID })).toBe("/tasks");
  });

  it("titles an entry by its date and a review by its period", () => {
    expect(objectTitle("task", { space_id: "s1", title: "Appeler le club" })).toBe("Appeler le club");
    expect(objectTitle("journal", { space_id: "s1", entry_date: "2026-11-03" })).toMatch(/2026/);
    expect(
      objectTitle("review", {
        space_id: "s1",
        period: "weekly",
        period_start: "2026-10-26",
        period_end: "2026-11-01",
      }),
    ).toBe("Semaine du 26 octobre au 1er novembre 2026");
  });
});

describe("agentContextIssue", () => {
  it("mirrors the server: the agent must read that kind of object", () => {
    expect(agentContextIssue(undefined, "task")).toBeNull();
    expect(agentContextIssue(agent(["goals", "journal", "reviews"]), "goal")).toBeNull();
    expect(agentContextIssue(agent(["goals", "journal", "reviews"]), "journal")).toBeNull();
    expect(agentContextIssue(agent(["goals", "journal", "reviews"]), "review")).toBeNull();
    expect(agentContextIssue(agent(["goals", "journal", "reviews"]), "task")).toBe("not_a_resource");
    expect(agentContextIssue(agent(["tasks", "goals", "calendar"]), "journal")).toBe("not_a_resource");
    expect(agentContextIssue(agent(["finances", "documents"]), "review")).toBe("not_a_resource");
    expect(agentContextIssue(agent(["tasks", "goals"], ["goals"]), "goal")).toBe("not_granted");
    expect(agentContextIssue(agent(["goals", "journal", "reviews"], ["reviews"]), "review")).toBe("not_granted");
  });

  it("names the resource as the server does", () => {
    expect([contextResource("task"), contextResource("goal"), contextResource("journal"), contextResource("review")]).toEqual([
      "tasks",
      "goals",
      "journal",
      "reviews",
    ]);
    expect(resourceLabel("journal")).toBe("entrées de journal");
    expect(resourceLabel("review")).toBe("bilans");
  });
});
