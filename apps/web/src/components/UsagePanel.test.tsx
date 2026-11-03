import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LLMUsage } from "@/lib/types";

import { UsagePanel } from "./UsagePanel";

type Call = LLMUsage["recent_calls"][number];

function call(overrides: Partial<Call>): Call {
  return {
    id: "c1",
    space_id: "s1",
    purpose: "chat.reply",
    provider: "gemini",
    tier: "fast",
    model: null,
    cost_usd: "0",
    input_tokens: 0,
    output_tokens: 0,
    redactions: 0,
    outcome: "ok",
    error_code: null,
    duration_ms: 0,
    created_at: "2026-10-18T08:00:00Z",
    ...overrides,
  };
}

const usage: LLMUsage = {
  provider: "gemini",
  enabled: true,
  paused: false,
  billing: "free",
  models: { fast: "gemini-fast", deep: "gemini-deep" },
  requests_today: { fast: 3, deep: 0 },
  in_flight: { fast: 1, deep: 0 },
  daily_request_caps: { fast: 398, deep: 13 },
  daily_request_margin: 2,
  quota_resets_at: "2026-10-19T07:00:00Z",
  month_tokens_used: 1200,
  month_token_budget: 1_000_000,
  cost_today_usd: "0",
  cost_month_usd: "0",
  daily_budget_usd: 0.475,
  monthly_budget_usd: 4.75,
  budget_margin_ratio: 0.05,
  recent_calls: [
    call({ id: "c3", outcome: "reserved", created_at: "2026-10-18T08:02:00Z" }),
    call({ id: "c2", outcome: "abandoned", created_at: "2026-10-18T08:01:00Z" }),
    call({ id: "c1", outcome: "ok", model: "gemini-fast", input_tokens: 120, output_tokens: 40 }),
  ],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPanel(body: LLMUsage) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json(200, body)),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <UsagePanel />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("UsagePanel", () => {
  it("shows the calls in flight, the enforced caps and the safety margin", async () => {
    renderPanel(usage);
    expect(await screen.findByText("dont 1 appel en cours")).toBeTruthy();
    const fast = screen.getByRole("progressbar", { name: "Requêtes du jour, niveau rapide" });
    expect(fast.getAttribute("aria-valuetext")).toMatch(/^3 \/ 398/);
    expect(screen.getByText(/Marge de sécurité : 2 requêtes gardées sous chaque plafond configuré\./)).toBeTruthy();
    expect(screen.getByText(/les appels en cours comptent déjà/)).toBeTruthy();
  });

  it("labels reserved and abandoned calls in the recent calls", async () => {
    renderPanel(usage);
    const table = await screen.findByRole("table", { name: "Derniers appels à l'IA" });
    expect(within(table).getByText("En cours")).toBeTruthy();
    expect(within(table).getByText("Abandonné")).toBeTruthy();
    expect(within(table).getByText("Réussi")).toBeTruthy();
  });

  it("mentions the calls in flight on paid billing too", async () => {
    renderPanel({ ...usage, billing: "paid", in_flight: { fast: 0, deep: 2 }, requests_today: { fast: 3, deep: 2 } });
    expect(await screen.findByText(/2 requêtes aujourd.hui, dont 2 appels en cours/)).toBeTruthy();
    expect(screen.getByText(/5 % des budgets sont gardés en réserve/)).toBeTruthy();
    expect(screen.queryByText("dont 1 appel en cours")).toBeNull();
  });
});
