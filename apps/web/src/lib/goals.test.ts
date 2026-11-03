import { describe, expect, it } from "vitest";

import {
  buildCheckin,
  buildGoalCreate,
  buildGoalUpdate,
  decimalToInput,
  dueHint,
  EMPTY_GOAL_FORM,
  formatDecimal,
  formatPercent,
  goalsPath,
  goalToForm,
  parseDecimal,
  progressLabel,
  progressPercent,
  sortGoalTasks,
  statusActions,
  suggestAchieved,
  tasksLabel,
} from "./goals";
import type { GoalProgress, GoalSummary, Task } from "./types";

const NBSP = "\u00a0";

function progress(overrides: Partial<GoalProgress> = {}): GoalProgress {
  return {
    ratio: null,
    basis: "none",
    milestones_done: 0,
    milestones_total: 0,
    tasks_open: 0,
    tasks_done: 0,
    ...overrides,
  };
}

function goal(overrides: Partial<GoalSummary> = {}): GoalSummary {
  return {
    id: "g1",
    space_id: "s1",
    title: "Courir un semi",
    description: null,
    motivation: null,
    kind: "qualitative",
    metric_unit: null,
    start_value: null,
    target_value: null,
    current_value: null,
    start_date: null,
    due_date: null,
    status: "active",
    review_cadence: "weekly",
    achieved_at: null,
    version: 1,
    created_at: "2026-10-01T08:00:00Z",
    updated_at: "2026-10-01T08:00:00Z",
    progress: progress(),
    ...overrides,
  };
}

// Not typed as Task on purpose: stays valid if Task gains `goal_id` (sent by the API).
const TASK_BASE: Task = {
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
  return { ...TASK_BASE, ...overrides };
}

describe("formatDecimal", () => {
  it("formats API decimals the French way, with the unit", () => {
    expect(formatDecimal("12.50")).toBe("12,5");
    expect(formatDecimal("70.00", "kg")).toBe(`70${NBSP}kg`);
    expect(formatDecimal("-3.25", " % ")).toBe(`-3,25${NBSP}%`);
    expect(formatDecimal("1234567.80")).toBe("1\u202f234\u202f567,8");
  });

  it("returns an empty string without a value", () => {
    expect(formatDecimal(null, "kg")).toBe("");
    expect(formatDecimal(undefined)).toBe("");
  });
});

describe("decimal inputs", () => {
  it("parses French input into API decimals", () => {
    expect(parseDecimal("12,5")).toEqual({ ok: true, value: "12.5" });
    expect(parseDecimal(" 1 234,75 ")).toEqual({ ok: true, value: "1234.75" });
    expect(parseDecimal("+7.")).toEqual({ ok: true, value: "7" });
    expect(parseDecimal(",5")).toEqual({ ok: true, value: "0.5" });
    expect(parseDecimal("-.5")).toEqual({ ok: true, value: "-0.5" });
    expect(parseDecimal("")).toEqual({ ok: true, value: null });
  });

  it("rejects what the API would refuse", () => {
    expect(parseDecimal("12,345").ok).toBe(false);
    expect(parseDecimal("abc").ok).toBe(false);
    expect(parseDecimal("1234567890123").ok).toBe(false);
    expect(parseDecimal("1,2,3").ok).toBe(false);
  });

  it("turns API decimals back into editable values", () => {
    expect(decimalToInput("12.50")).toBe("12,5");
    expect(decimalToInput("70.00")).toBe("70");
    expect(decimalToInput("100")).toBe("100");
    expect(decimalToInput(null)).toBe("");
  });
});

describe("progress", () => {
  it("labels a metric goal with current and target values", () => {
    const g = goal({
      kind: "measurable",
      metric_unit: "kg",
      current_value: "72.50",
      target_value: "70.00",
      progress: progress({ basis: "metric", ratio: 0.75 }),
    });
    expect(progressLabel(g)).toBe(`72,5 / 70${NBSP}kg`);
  });

  it("labels milestones and empty progress", () => {
    expect(progressLabel(goal({ progress: progress({ basis: "milestones", milestones_done: 2, milestones_total: 5 }) }))).toBe(
      "2/5 jalons",
    );
    expect(progressLabel(goal({ progress: progress({ basis: "milestones", milestones_done: 0, milestones_total: 1 }) }))).toBe(
      "0/1 jalon",
    );
    expect(progressLabel(goal())).toBe("Rien à mesurer pour l'instant");
  });

  it("clamps and rounds percentages", () => {
    expect(progressPercent(0.456)).toBe(46);
    expect(progressPercent(1.4)).toBe(100);
    expect(progressPercent(-1)).toBe(0);
    expect(progressPercent(null)).toBeNull();
    expect(formatPercent(0.5)).toBe(`50${NBSP}%`);
    expect(formatPercent(null)).toBe("");
  });

  it("suggests marking an open goal achieved once fully measured", () => {
    expect(suggestAchieved(goal({ progress: progress({ basis: "metric", ratio: 1 }) }))).toBe(true);
    expect(suggestAchieved(goal({ status: "paused", progress: progress({ basis: "milestones", ratio: 1 }) }))).toBe(true);
    expect(suggestAchieved(goal({ progress: progress({ basis: "metric", ratio: 0.9 }) }))).toBe(false);
    expect(suggestAchieved(goal({ status: "achieved", progress: progress({ basis: "metric", ratio: 1 }) }))).toBe(false);
  });
});

describe("labels", () => {
  it("offers status transitions from the current status", () => {
    expect(statusActions("active").map((a) => a.label)).toEqual([
      "Mettre en pause",
      "Marquer comme atteint",
      "Abandonner",
      "Archiver",
    ]);
    expect(statusActions("archived")).toEqual([{ status: "active", label: "Réactiver" }]);
    expect(statusActions("achieved").map((a) => a.status)).toEqual(["active", "archived"]);
  });

  it("counts linked tasks in French", () => {
    expect(tasksLabel(0, 0)).toBe("Aucune tâche liée");
    expect(tasksLabel(1, 0)).toBe("1 tâche ouverte");
    expect(tasksLabel(3, 1)).toBe("3 tâches ouvertes · 1 terminée");
    expect(tasksLabel(0, 2)).toBe("2 tâches terminées");
    expect(tasksLabel(0, 1)).toBe("1 tâche terminée");
  });

  it("describes due dates relative to today", () => {
    expect(dueHint(null, "2026-10-11")).toBeNull();
    expect(dueHint("2026-10-11", "2026-10-11")).toEqual({ text: "aujourd'hui", overdue: false });
    expect(dueHint("2026-10-12", "2026-10-11")).toEqual({ text: "demain", overdue: false });
    expect(dueHint("2026-11-10", "2026-10-11")).toEqual({ text: "dans 30 jours", overdue: false });
    expect(dueHint("2026-10-10", "2026-10-11")).toEqual({ text: "en retard d'un jour", overdue: true });
    expect(dueHint("2026-10-01", "2026-10-11")).toEqual({ text: "en retard de 10 jours", overdue: true });
  });

  it("builds the list path", () => {
    expect(goalsPath(null, "active")).toBe("/v1/goals?status=active");
    expect(goalsPath("s1", "all")).toBe("/v1/goals?space_id=s1");
    expect(goalsPath(null, "all")).toBe("/v1/goals");
  });
});

describe("sortGoalTasks", () => {
  it("lists open tasks by due date, then closed ones", () => {
    const sorted = sortGoalTasks([
      task({ id: "done", status: "done", completed_at: "2026-10-05T10:00:00Z" }),
      task({ id: "nodue", created_at: "2026-10-03T08:00:00Z" }),
      task({ id: "late", due_at: "2026-10-20T08:00:00Z" }),
      task({ id: "soon", status: "in_progress", due_at: "2026-10-12T08:00:00Z" }),
      task({ id: "cancelled", status: "cancelled", updated_at: "2026-10-06T10:00:00Z" }),
    ]);
    expect(sorted.map((t) => t.id)).toEqual(["soon", "late", "nodue", "cancelled", "done"]);
  });
});

describe("goal forms", () => {
  it("builds a measurable creation body", () => {
    const result = buildGoalCreate(
      {
        ...EMPTY_GOAL_FORM,
        title: "  Perdre du poids ",
        kind: "measurable",
        metric_unit: "kg",
        start_value: "80",
        target_value: "72,5",
        due_date: "2027-01-31",
        motivation: "Mieux dormir",
      },
      "s1",
    );
    expect(result).toEqual({
      ok: true,
      value: {
        space_id: "s1",
        kind: "measurable",
        title: "Perdre du poids",
        description: null,
        motivation: "Mieux dormir",
        metric_unit: "kg",
        start_value: "80",
        target_value: "72.5",
        start_date: null,
        due_date: "2027-01-31",
        review_cadence: "weekly",
      },
    });
  });

  it("requires a target for a measurable goal and coherent dates", () => {
    expect(buildGoalCreate({ ...EMPTY_GOAL_FORM, title: "x", kind: "measurable" }, "s1")).toEqual({
      ok: false,
      error: "Un objectif mesurable demande une valeur cible.",
    });
    // Without a starting point, progress would be ambiguous (lose 10 kg vs read 20 books).
    expect(
      buildGoalCreate({ ...EMPTY_GOAL_FORM, title: "x", kind: "measurable", target_value: "70" }, "s1"),
    ).toEqual({
      ok: false,
      error: "Un objectif mesurable demande une valeur de départ (0 si vous partez de zéro).",
    });
    expect(buildGoalCreate({ ...EMPTY_GOAL_FORM, title: " " }, "s1").ok).toBe(false);
    expect(
      buildGoalCreate({ ...EMPTY_GOAL_FORM, title: "x", start_date: "2026-10-11", due_date: "2026-10-01" }, "s1").ok,
    ).toBe(false);
  });

  it("drops metric fields of a qualitative goal", () => {
    const result = buildGoalCreate({ ...EMPTY_GOAL_FORM, title: "x", metric_unit: "kg", target_value: "3" }, "s1");
    expect(result.ok && result.value.target_value).toBeNull();
  });

  it("sends only the changed fields on update", () => {
    const g = goal({ kind: "measurable", metric_unit: "kg", start_value: "80.00", target_value: "70.00", current_value: "75.00" });
    expect(buildGoalUpdate(g, goalToForm(g))).toEqual({ ok: true, value: {} });
    expect(buildGoalUpdate(g, { ...goalToForm(g), target_value: "68,5", due_date: "2027-03-01", description: "  " })).toEqual({
      ok: true,
      value: { target_value: "68.5", due_date: "2027-03-01" },
    });
    expect(buildGoalUpdate(g, { ...goalToForm(g), target_value: "" }).ok).toBe(false);
  });

  it("clears an emptied due date", () => {
    const g = goal({ due_date: "2026-12-31" });
    expect(buildGoalUpdate(g, { ...goalToForm(g), due_date: "" })).toEqual({ ok: true, value: { due_date: null } });
  });

  it("requires a value or a note for a check-in", () => {
    expect(buildCheckin("measurable", "74,2", "")).toEqual({ ok: true, value: { value: "74.2", note: null } });
    expect(buildCheckin("measurable", "", "  ")).toEqual({ ok: false, error: "Indiquez une valeur ou une note." });
    expect(buildCheckin("qualitative", "12", "Bonne semaine")).toEqual({
      ok: true,
      value: { value: null, note: "Bonne semaine" },
    });
    expect(buildCheckin("measurable", "1,234", "note").ok).toBe(false);
  });
});
