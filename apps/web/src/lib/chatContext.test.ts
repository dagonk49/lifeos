import { describe, expect, it } from "vitest";

import { agentContextIssue, chatHref, contextLabel, linkedLabel, objectHref, parseAbout } from "./chatContext";
import type { AgentInfo } from "./types";

const ID = "0b9c8f6e-1d2a-4c3b-9e8f-7a6b5c4d3e2f";

function agent(resources: string[], missing: string[] = []): AgentInfo {
  return { name: "coaching", label: "Coaching", description: "", resources, missing_permissions: missing };
}

describe("parseAbout", () => {
  it("accepts a task or a goal with a UUID", () => {
    expect(parseAbout(`task:${ID}`)).toEqual({ type: "task", id: ID });
    expect(parseAbout(`goal:${ID.toUpperCase()}`)).toEqual({ type: "goal", id: ID });
  });

  it("ignores an unknown type, an id that is not a UUID or an empty parameter", () => {
    expect(parseAbout(`journal:${ID}`)).toBeNull();
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
    expect(chatHref({ type: "task", id: "a b&c" })).toBe("/chat?about=task:a%20b%26c");
  });

  it("names the object and where it lives", () => {
    expect(contextLabel("goal")).toBe("Objectif");
    expect(contextLabel("task")).toBe("Tâche");
    expect(linkedLabel("goal")).toBe("Objectif lié");
    expect(linkedLabel("task")).toBe("Tâche liée");
    expect(objectHref({ type: "goal", id: ID })).toBe(`/goals?id=${ID}`);
    expect(objectHref({ type: "task", id: ID })).toBe("/tasks");
  });
});

describe("agentContextIssue", () => {
  it("mirrors the server: the agent must read that kind of object", () => {
    expect(agentContextIssue(undefined, "task")).toBeNull();
    expect(agentContextIssue(agent(["goals", "journal", "reviews"]), "goal")).toBeNull();
    expect(agentContextIssue(agent(["goals", "journal", "reviews"]), "task")).toBe("not_a_resource");
    expect(agentContextIssue(agent(["tasks", "goals"], ["goals"]), "goal")).toBe("not_granted");
  });
});
