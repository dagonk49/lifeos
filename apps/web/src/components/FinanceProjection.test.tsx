import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { financeKeys } from "@/lib/finance";
import { SpaceProvider } from "@/lib/space";
import type { FinanceProjection as Projection, ProjectionMonth, Space } from "@/lib/types";

import { FinanceProjection } from "./FinanceProjection";

const SPACES: Space[] = [{ id: "s1", kind: "personal", name: "Perso" }];

function month(overrides: Partial<ProjectionMonth>): ProjectionMonth {
  return {
    month: "2026-11",
    state: "future",
    known_income: "0.00",
    known_expenses: "0.00",
    scheduled_income: "2000.00",
    scheduled_expenses: "750.00",
    estimated_income: "0.00",
    estimated_income_low: "0.00",
    estimated_income_high: "0.00",
    estimated_expenses: "330.00",
    estimated_expenses_low: "300.00",
    estimated_expenses_high: "360.00",
    balance: "920.00",
    balance_low: "890.00",
    balance_high: "950.00",
    cumulative: "920.00",
    cumulative_low: "890.00",
    cumulative_high: "950.00",
    items: [
      { label: "Loyer", direction: "expense", basis: "scheduled", source: "recurring", amount: "750.00", low: "750.00", high: "750.00", occurrences: 1, recurring_id: "r1", category_id: null },
      { label: "Alimentation", direction: "expense", basis: "estimated", source: "baseline", amount: "330.00", low: "300.00", high: "360.00", occurrences: 0, recurring_id: null, category_id: "c1" },
    ],
    ...overrides,
  };
}

function projection(balance: string): Projection {
  return {
    space_id: "s1",
    currency: "EUR",
    today: "2026-10-28",
    horizon: 1,
    baseline: { months: 3, start: "2026-07", end: "2026-09" },
    months: [
      month({ month: "2026-10", state: "current", balance: "1000.00", known_income: "2000.00", known_expenses: "1000.00", scheduled_income: "0.00", scheduled_expenses: "0.00", items: [] }),
      month({ balance }),
    ],
    savings_goals: [
      {
        goal_id: "g1",
        title: "Vacances",
        currency: "EUR",
        target: "1500.00",
        saved: "400.00",
        saved_at: "2026-10-20",
        remaining: "1100.00",
        reached: false,
        contribution: "200.00",
        contribution_source: "declared",
        eta_month: "2027-05",
        eta_early: "2027-05",
        eta_late: "2027-05",
        due_date: "2027-03-31",
        on_track: false,
      },
    ],
    assumptions: [{ code: "no_inflation", label: "Aucune inflation ni hausse de prix.", value: null }],
    warnings: [{ code: "short_history", label: "Seulement 1 mois complet(s) d'historique.", value: "1" }],
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FinanceProjection", () => {
  it("tells known, scheduled and estimated apart, with assumptions, and refetches after a change", async () => {
    let balance = "920.00";
    const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify(projection(balance)), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <SpaceProvider spaces={SPACES}>
          <FinanceProjection spaceId="s1" />
        </SpaceProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Aucune inflation ni hausse de prix.")).toBeTruthy();
    expect(screen.getByText("Seulement 1 mois complet(s) d'historique.")).toBeTruthy();
    expect(screen.getAllByText("connu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("prévu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("estimé").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/330,00\s€ \(entre 300,00\s€ et 360,00\s€\)/).length).toBeGreaterThan(0);
    expect(screen.getByText(/^\+920,00\s€$/)).toBeTruthy();
    expect(screen.getByText(/400,00\s€ épargnés sur 1\s500,00\s€/)).toBeTruthy();
    expect(screen.getByText(/En retard sur l'échéance du 31 mars 2027/)).toBeTruthy();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/v1/finance/projection?space_id=s1&months=6&baseline_months=3");

    // A correction elsewhere invalidates ["finance", "projection"]: the figures follow.
    balance = "870.00";
    await act(() => client.invalidateQueries({ queryKey: financeKeys.projections }));
    await waitFor(() => expect(screen.getByText(/^\+870,00\s€$/)).toBeTruthy());
  });
});
