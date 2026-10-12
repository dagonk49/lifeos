import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { GoalDetail } from "@/lib/types";

import { GoalDetailPanel } from "./GoalDetail";

const goal: GoalDetail = {
  id: "g1",
  space_id: "s1",
  title: "Perdre 5 kg",
  description: null,
  motivation: "Mieux dormir",
  kind: "measurable",
  metric_unit: "kg",
  start_value: "80.00",
  target_value: "75.00",
  current_value: "78.00",
  start_date: null,
  due_date: null,
  status: "active",
  review_cadence: "weekly",
  achieved_at: null,
  version: 3,
  created_at: "2026-10-01T08:00:00Z",
  updated_at: "2026-10-01T08:00:00Z",
  progress: { ratio: 0.4, basis: "metric", milestones_done: 0, milestones_total: 0, tasks_open: 0, tasks_done: 0 },
  milestones: [],
  checkins: [],
};

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function mockApi(): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const method = init.method ?? "GET";
      calls.push({
        url,
        method,
        headers: init.headers as Record<string, string>,
        body: init.body ? JSON.parse(init.body as string) : undefined,
      });
      let data: unknown = goal;
      if (url.startsWith("/v1/tasks")) data = { items: [], next_cursor: null };
      else if (method === "PATCH") data = { ...goal, status: "paused", version: 4 };
      else if (url.endsWith("/checkins")) data = { id: "c1", value: "77.5", note: null, created_at: goal.created_at };
      return new Response(JSON.stringify(data), { status: method === "POST" ? 201 : 200 });
    }),
  );
  return calls;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Perso" }]}>
        <GoalDetailPanel goalId="g1" onClose={() => {}} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("GoalDetailPanel", () => {
  it("shows the measured progress and pauses with the current version", async () => {
    const calls = mockApi();
    renderPanel();
    expect(await screen.findByRole("heading", { name: "Perdre 5 kg" })).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40");
    // The DOM matcher turns the non-breaking space before the unit into a plain one.
    expect(screen.getByText("78 / 75 kg")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Mettre en pause" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/v1/goals/g1");
    expect(patch?.headers["If-Match"]).toBe("3");
    expect(patch?.body).toEqual({ status: "paused" });
  });

  it("records a check-in typed with a French decimal comma", async () => {
    const calls = mockApi();
    renderPanel();
    fireEvent.change(await screen.findByLabelText(/^Valeur actuelle \(kg\)/), { target: { value: "77,5" } });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer le point" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/checkins"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/checkins"))?.body).toEqual({ value: "77.5", note: null });
  });
});
