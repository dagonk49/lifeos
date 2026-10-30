import { describe, expect, it } from "vitest";

import {
  blockedHint,
  blockedLabel,
  dependencyCandidates,
  knownTags,
  normalizeTag,
  parseTags,
  sortTasks,
} from "./tasks";
import type { Task, TaskBlocker } from "./types";

const BASE: Task = {
  id: "t",
  space_id: "s1",
  title: "t",
  notes: null,
  status: "todo",
  priority: "normal",
  due_at: null,
  remind_at: null,
  completed_at: null,
  source_type: "manual",
  source_id: null,
  goal_id: null,
  tags: [],
  depends_on: [],
  blocked_by: [],
  blocked: false,
  version: 1,
  created_at: "2026-10-01T08:00:00Z",
  updated_at: "2026-10-01T08:00:00Z",
};

function task(overrides: Partial<Task>): Task {
  return { ...BASE, ...overrides };
}

function blocker(title: string): TaskBlocker {
  return { id: title, title, status: "todo" };
}

describe("blockedLabel", () => {
  it("names what the task waits for", () => {
    expect(blockedLabel([])).toBeNull();
    expect(blockedLabel([blocker("Devis")])).toBe("Bloquée par « Devis »");
    expect(blockedLabel([blocker("A"), blocker("B")])).toBe("Bloquée par « A » et « B »");
    expect(blockedLabel([blocker("A"), blocker("B"), blocker("C")])).toBe("Bloquée par « A », « B » et 1 autre");
    expect(blockedLabel(["A", "B", "C", "D"].map(blocker))).toBe("Bloquée par « A », « B » et 2 autres");
  });

  it("explains how to free it", () => {
    expect(blockedHint([blocker("Devis")])).toBe("Bloquée par « Devis » : terminez ou annulez d'abord cette tâche.");
    expect(blockedHint([blocker("A"), blocker("B")])).toMatch(/d'abord ces tâches\.$/);
    expect(blockedHint([])).toBeNull();
  });
});

describe("tags", () => {
  it("normalises like the API", () => {
    expect(normalizeTag("  #Maison   Principale ")).toBe("maison principale");
    expect(normalizeTag("##URGENT")).toBe("urgent");
    expect(normalizeTag("   ")).toBe("");
  });

  it("parses a comma-separated list without duplicates or empty labels", () => {
    expect(parseTags("Maison, #maison, , Urgent")).toEqual({ ok: true, tags: ["maison", "urgent"] });
    expect(parseTags("")).toEqual({ ok: true, tags: [] });
  });

  it("refuses what the API would refuse", () => {
    expect(parseTags("x".repeat(41)).ok).toBe(false);
    expect(parseTags(Array.from({ length: 11 }, (_, i) => `t${i}`).join(",")).ok).toBe(false);
    expect(parseTags(Array.from({ length: 10 }, (_, i) => `t${i}`).join(",")).ok).toBe(true);
  });

  it("lists the labels in use", () => {
    expect(knownTags([task({ tags: ["école", "maison"] }), task({ tags: ["maison", "argent"] })])).toEqual([
      "argent",
      "école",
      "maison",
    ]);
  });
});

describe("sortTasks", () => {
  it("puts blocked tasks last, then sorts by due date and recency", () => {
    const sorted = sortTasks([
      task({ id: "blocked", blocked: true, due_at: "2026-10-02T08:00:00Z" }),
      task({ id: "no-due-old", created_at: "2026-09-01T08:00:00Z" }),
      task({ id: "late", due_at: "2026-10-05T08:00:00Z" }),
      task({ id: "soon", due_at: "2026-10-03T08:00:00Z" }),
      task({ id: "no-due-new", created_at: "2026-10-01T09:00:00Z" }),
    ]);
    expect(sorted.map((t) => t.id)).toEqual(["soon", "late", "no-due-new", "no-due-old", "blocked"]);
  });
});

describe("dependencyCandidates", () => {
  it("offers the other open tasks of the same space, not yet linked", () => {
    const self = task({ id: "self", title: "Moi", depends_on: ["linked"] });
    const tasks = [
      self,
      task({ id: "linked", title: "Déjà liée" }),
      task({ id: "other-space", title: "Ailleurs", space_id: "s2" }),
      task({ id: "done", title: "Finie", status: "done" }),
      task({ id: "b", title: "Banque", status: "in_progress" }),
      task({ id: "a", title: "Assurance" }),
    ];
    expect(dependencyCandidates(tasks, "s1", self).map((t) => t.id)).toEqual(["a", "b"]);
    expect(dependencyCandidates(tasks, "s1").map((t) => t.id)).toEqual(["a", "b", "linked", "self"]);
  });
});
