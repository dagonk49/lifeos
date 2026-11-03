import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PurchaseAdvisor } from "@/components/PurchaseAdvisor";
import type { PurchaseResult } from "@/lib/types";

function result(overrides: Partial<PurchaseResult> = {}): PurchaseResult {
  return {
    run_id: "r1",
    space_id: "s1",
    created_at: "2026-10-30T10:00:00Z",
    question: { space_id: "s1", amount: "450.00", label: "Vélo" },
    steps: [
      {
        agent: "finance",
        label: "Finances",
        role: "Calcule la marge budgétaire, localement",
        resources_read: ["finances"],
        missing: [],
        items: 4,
        cloud_calls: 0,
        duration_ms: 2,
      },
      {
        agent: "coaching",
        label: "Coaching",
        role: "Compare l'achat aux objectifs actifs, localement",
        resources_read: ["goals"],
        missing: [],
        items: 1,
        cloud_calls: 0,
        duration_ms: 1,
      },
      {
        agent: "core",
        label: "Assistant général",
        role: "Formule la recommandation",
        resources_read: [],
        missing: [],
        items: 2,
        cloud_calls: 1,
        duration_ms: 5,
      },
    ],
    figures: [
      {
        key: "F1",
        agent: "finance",
        label: "Solde moyen par mois",
        value: "500.00",
        unit: "EUR",
        low: null,
        high: null,
        basis: "moyenne de 3 mois",
      },
    ],
    goals: [
      {
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
      },
    ],
    hypotheses: [{ agent: "finance", text: "Mois en cours incomplet." }],
    advice: {
      verdict: "à reporter",
      confidence: "faible",
      recommendation: "Attendez le mois prochain.",
      reasons: [{ text: "Le solde moyen couvre l'achat.", refs: ["F1"], agents: ["finance"], supported: true }],
      hypotheses: [],
      uncertainties: ["Revenus variables."],
      unknown_refs: [],
      unmatched_amounts: ["999 €"],
    },
    error: null,
    cloud_calls: 1,
    max_cloud_calls: 1,
    ...overrides,
  };
}

type Call = { url: string; method: string; body: unknown; headers: Record<string, string> };

function mockApi(reply: PurchaseResult): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({
        url,
        method,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
        headers: (init.headers ?? {}) as Record<string, string>,
      });
      if (method === "DELETE") return new Response(null, { status: 204 });
      return new Response(JSON.stringify(reply), { status: 201, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

function renderAdvisor() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <PurchaseAdvisor spaceId="s1" />
    </QueryClientProvider>,
  );
}

function fill(label: string, amount: string) {
  fireEvent.change(screen.getByLabelText("Ce que vous envisagez d'acheter"), { target: { value: label } });
  fireEvent.change(screen.getByLabelText("Montant (€)"), { target: { value: amount } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PurchaseAdvisor", () => {
  it("sends the exact amount once and shows who said what on which data", async () => {
    const calls = mockApi(result());
    renderAdvisor();
    fill("Vélo", "450,00");
    fireEvent.click(screen.getByRole("button", { name: "Évaluer l'achat" }));

    expect(await screen.findByText("Attendez le mois prochain.")).toBeTruthy();
    const [post] = calls;
    expect(post).toMatchObject({ url: "/v1/agents/workflows/purchase", method: "POST" });
    expect(post?.body).toEqual({ space_id: "s1", amount: "450.00", label: "Vélo" });
    expect(post?.headers["Idempotency-Key"]).toBeTruthy();

    expect(screen.getByText("Achat à reporter")).toBeTruthy();
    expect(screen.getByText(/Selon l'agent Finances \(F1\)/)).toBeTruthy();
    expect(screen.getByText(/ne correspondent à aucun calcul : 999 €/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Chiffres de l'agent Finances/ })).toBeTruthy();
    expect(screen.getByText("Fonds pour vacances")).toBeTruthy();
    expect(screen.getByText(/1 appel à l'IA sur 1 autorisé/)).toBeTruthy();
  });

  it("checks the fields before sending anything", () => {
    const calls = mockApi(result());
    renderAdvisor();
    fill("", "12,345");
    fireEvent.click(screen.getByRole("button", { name: "Évaluer l'achat" }));
    expect(screen.getByText(/Indiquez ce que vous envisagez d'acheter/)).toBeTruthy();
    expect(screen.getByText(/Deux décimales au plus/)).toBeTruthy();
    expect(calls).toEqual([]);
  });

  it("shows the local figures and the missing agent in degraded mode", async () => {
    mockApi(
      result({
        advice: null,
        cloud_calls: 0,
        error: { code: "agent_permission_required", title: "x", detail: null, missing: ["goals"] },
        steps: [
          result().steps[0]!,
          { ...result().steps[1]!, resources_read: [], missing: ["goals"], items: 0 },
          { ...result().steps[2]!, cloud_calls: 0 },
        ],
        goals: [],
      }),
    );
    renderAdvisor();
    fill("Vélo", "450");
    fireEvent.click(screen.getByRole("button", { name: "Évaluer l'achat" }));
    expect(await screen.findByText(/Sans accès, l'agent Coaching n'a rien lu/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Gérer l'accès des agents" }).getAttribute("href")).toBe(
      "/settings#agents",
    );
    expect(screen.getByText(/Pas d'accès en lecture aux objectifs/)).toBeTruthy();
    expect(screen.getByText("Solde moyen par mois", { exact: false })).toBeTruthy();
  });

  it("erases the evaluation after a named confirmation", async () => {
    const calls = mockApi(result());
    renderAdvisor();
    fill("Vélo", "450");
    fireEvent.click(screen.getByRole("button", { name: "Évaluer l'achat" }));
    fireEvent.click(await screen.findByRole("button", { name: "Effacer cette évaluation" }));
    fireEvent.click(screen.getByRole("button", { name: "Effacer l'évaluation « Vélo »" }));
    await waitFor(() => expect(screen.queryByText("Attendez le mois prochain.")).toBeNull());
    expect(calls.at(-1)).toMatchObject({ url: "/v1/agents/runs/r1", method: "DELETE" });
  });
});
