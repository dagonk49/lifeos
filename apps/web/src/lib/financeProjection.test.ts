import { describe, expect, it } from "vitest";

import {
  basisLabel,
  CATEGORY_CHANGE_KEYS,
  cadenceLabel,
  emptyRecurringForm,
  financeKeys,
  projectionPath,
  rangeText,
  RECURRING_CHANGE_KEYS,
  recurringAmountText,
  TRANSACTION_CHANGE_KEYS,
  validateRecurring,
} from "./finance";
import { buildGoalCreate, buildGoalUpdate, EMPTY_GOAL_FORM, formatGoalValue, goalToForm } from "./goals";
import type { Goal } from "./types";

describe("projection helpers", () => {
  it("names each basis in words", () => {
    expect([basisLabel("known"), basisLabel("scheduled"), basisLabel("estimated")]).toEqual(["connu", "prévu", "estimé"]);
    expect(cadenceLabel("quarterly")).toBe("Chaque trimestre");
  });

  it("formats ranges exactly", () => {
    expect(rangeText("60.00", "120.00")).toBe("entre 60,00 € et 120,00 €");
    expect(rangeText("-0.01", "999999999999.99")).toBe("entre −0,01 € et 999 999 999 999,99 €");
    expect(rangeText("80.00", "80.00")).toBe("80,00 €");
  });

  it("refreshes the projection after any finance change", () => {
    const has = (keys: readonly (readonly string[])[]) => keys.some((k) => k.join("/") === "finance/projection");
    expect(has(TRANSACTION_CHANGE_KEYS)).toBe(true);
    expect(has(CATEGORY_CHANGE_KEYS)).toBe(true);
    expect(has(RECURRING_CHANGE_KEYS)).toBe(true);
    expect(financeKeys.projection("s1", 6, 3).slice(0, 2)).toEqual(["finance", "projection"]);
    expect(projectionPath("s1", 6, 3)).toBe("/v1/finance/projection?space_id=s1&months=6&baseline_months=3");
  });

  it("validates a recurring item like the server", () => {
    const form = { ...emptyRecurringForm("2026-10-28"), label: " Loyer ", amount: "750" };
    expect(validateRecurring(form)).toEqual({
      ok: true,
      value: {
        label: "Loyer",
        amount: "750.00",
        direction: "expense",
        cadence: "monthly",
        start_date: "2026-10-28",
        end_date: null,
        amount_is_estimate: false,
        amount_low: null,
        amount_high: null,
        category_id: null,
      },
    });
    expect(validateRecurring({ ...form, label: "" }).ok).toBe(false);
    expect(validateRecurring({ ...form, amount: "12,345" }).ok).toBe(false);
    expect(validateRecurring({ ...form, end_date: "2026-01-01" }).ok).toBe(false);
    expect(validateRecurring({ ...form, variable: true, low: "800", high: "900" })).toEqual({
      ok: false,
      error: "Le montant habituel doit se trouver entre le bas et le haut de la fourchette.",
    });
    const variable = validateRecurring({ ...form, variable: true, low: "700", high: "820,50" });
    expect(variable.ok && [variable.value.amount_low, variable.value.amount_high]).toEqual(["700.00", "820.50"]);
    expect(
      recurringAmountText({ amount: "80.00", amount_is_estimate: true, amount_low: "60.00", amount_high: "120.00", currency: "EUR" }),
    ).toBe("environ 80,00 € (entre 60,00 € et 120,00 €)");
  });
});

describe("savings goals", () => {
  const base = { ...EMPTY_GOAL_FORM, title: "Vacances", kind: "measurable" as const, start_value: "0", target_value: "1500" };

  it("sends a currency and an exact contribution", () => {
    const result = buildGoalCreate({ ...base, savings: true, monthly_contribution: "150,5" }, "s1");
    expect(result.ok && result.value).toMatchObject({
      metric_unit: "€",
      currency: "EUR",
      monthly_contribution: "150.50",
      target_value: "1500",
    });
    // A plain measurable goal sends neither.
    const plain = buildGoalCreate(base, "s1");
    expect(plain.ok && "currency" in plain.value).toBe(false);
    expect(buildGoalCreate({ ...base, savings: true, target_value: "0" }, "s1").ok).toBe(false);
    expect(buildGoalCreate({ ...base, savings: true, monthly_contribution: "1,234" }, "s1").ok).toBe(false);
  });

  it("formats euros exactly and updates only what changed", () => {
    const goal = {
      id: "g1",
      space_id: "s1",
      title: "Vacances",
      description: null,
      motivation: null,
      kind: "measurable",
      metric_unit: "€",
      start_value: "0.00",
      target_value: "1500.00",
      current_value: "1234.56",
      currency: "EUR",
      monthly_contribution: "150.00",
      start_date: null,
      due_date: null,
      status: "active",
      review_cadence: "weekly",
      achieved_at: null,
      version: 1,
      created_at: "2026-10-01T08:00:00Z",
      updated_at: "2026-10-01T08:00:00Z",
    } satisfies Goal;
    expect(formatGoalValue(goal, goal.current_value)).toBe("1 234,56 €");
    expect(formatGoalValue({ metric_unit: "kg", currency: null }, "12.50")).toBe("12,5 kg");
    const form = goalToForm(goal);
    expect(form.savings).toBe(true);
    expect(buildGoalUpdate(goal, form)).toEqual({ ok: true, value: {} });
    expect(buildGoalUpdate(goal, { ...form, monthly_contribution: "200" })).toEqual({
      ok: true,
      value: { monthly_contribution: "200.00" },
    });
    const stopped = buildGoalUpdate(goal, { ...form, savings: false, metric_unit: "€" });
    expect(stopped).toEqual({ ok: true, value: { currency: null, monthly_contribution: null } });
  });
});
