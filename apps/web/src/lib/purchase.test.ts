import { describe, expect, it } from "vitest";

import { figureRange, figureValue, goalSummary, missingAgents, reasonSource, stepSummary } from "./purchase";
import type { PurchaseGoal, PurchaseResult, PurchaseStep } from "./types";

const goal: PurchaseGoal = {
  key: "G1",
  agent: "coaching",
  goal_id: "g1",
  title: "Fonds pour vacances",
  progress: 40,
  progress_basis: "metric",
  due_date: null,
  financial: true,
  current: "1200.00",
  target: "3000.00",
  unit: "EUR",
  remaining: "1800.00",
  monthly_contribution: "300.00",
  purchase_months: "1.5",
};

function step(overrides: Partial<PurchaseStep>): PurchaseStep {
  return {
    agent: "finance",
    label: "Finances",
    role: "Calcule la marge",
    resources_read: ["finances"],
    missing: [],
    items: 4,
    cloud_calls: 0,
    duration_ms: 3,
    ...overrides,
  };
}

describe("purchase helpers", () => {
  it("formats figures in French by unit, without floats", () => {
    expect(figureValue({ value: "-150.00", unit: "EUR" })).toBe("−150,00 €");
    expect(figureValue({ value: "22.5", unit: "%" })).toBe("22,5 %");
    expect(figureValue({ value: "0.9", unit: "mois" })).toBe("0,9 mois");
    expect(figureRange({ low: "100.00", high: "300.00", unit: "EUR" })).toBe("entre 100,00 € et 300,00 €");
    expect(figureRange({ low: null, high: null, unit: "EUR" })).toBeNull();
  });

  it("summarises a goal from the coaching agent's findings", () => {
    expect(goalSummary(goal)).toBe(
      "40 % atteint · reste 1 800,00 € · l'achat représente 1,5 mois d'épargne prévue",
    );
    expect(goalSummary({ ...goal, progress: null, financial: false, purchase_months: null })).toBe(
      "avancement non mesuré",
    );
  });

  it("says which agent a reason rests on, or that it rests on nothing computed", () => {
    expect(reasonSource({ text: "x", refs: ["F8", "F10"], agents: ["finance"], supported: true })).toBe(
      "Selon l'agent Finances (F8, F10)",
    );
    expect(reasonSource({ text: "x", refs: ["F1", "G1"], agents: ["coaching", "finance"], supported: true })).toBe(
      "Selon les agents Coaching et Finances (F1, G1)",
    );
    expect(reasonSource({ text: "x", refs: [], agents: [], supported: false })).toBe(
      "Non appuyé sur un chiffre ou un objectif calculé",
    );
  });

  it("describes what each step read", () => {
    expect(stepSummary(step({}))).toBe("A lu : finances (4 éléments)");
    expect(stepSummary(step({ agent: "coaching", missing: ["goals"], resources_read: [] }))).toBe(
      "Pas d'accès en lecture aux objectifs : rien n'a été lu",
    );
    expect(stepSummary(step({ agent: "core", cloud_calls: 1 }), 1)).toBe(
      "1 appel à l'IA sur 1 autorisé, sur les seuls résultats des agents",
    );
  });

  it("lists the agents still to be granted", () => {
    const result = { steps: [step({ missing: ["finances"] }), step({ agent: "coaching" })] } as PurchaseResult;
    expect(missingAgents(result)).toEqual(["finance"]);
  });
});
