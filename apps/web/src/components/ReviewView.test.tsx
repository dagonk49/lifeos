import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Review } from "@/lib/types";

import { ReviewView } from "./ReviewView";

// Same structure as backend `compute_facts`.
const review: Review = {
  id: "r1",
  space_id: "s1",
  period: "weekly",
  period_start: "2026-10-05",
  period_end: "2026-10-11",
  facts: {
    period: {
      start: "2026-10-05",
      end: "2026-10-11",
      timezone: "Europe/Paris",
      computed_at: "2026-10-11T08:00:00+00:00",
      partial: true,
    },
    omitted: ["messages"],
    tasks: {
      completed: 2,
      completed_titles: ["Rappeler la banque", "Courses"],
      created: 4,
      overdue_open: 1,
      overdue_titles: ["Déclarer les impôts"],
      due_next_7_days: 3,
    },
    journal: { entries: 3, days_with_entry: 3, mood_average: 3.67, mood_entries: 3 },
    goals: [
      {
        title: "Courir 100 km",
        kind: "measurable",
        due_date: "2026-12-31",
        metric_progress: 0.42,
        current_value: "42",
        target_value: "100",
        unit: "km",
        checkins_in_period: 2,
        milestones_done_in_period: 0,
      },
    ],
    memory: { facts_added: 1, by_kind: { decision: 1 }, decisions: ["Changer de banque"], events: [] },
    sleep: { nights: 5 },
  },
  narrative: {
    summary: "Une semaine régulière.",
    progress: ["Deux tâches terminées"],
    obstacles: [],
    suggestions: [{ action: "Bloquer 30 minutes pour les impôts", why: "Tâche en retard" }],
    interpretations: ["Peut-être un manque de temps en fin de semaine"],
    questions: ["Qu'est-ce qui a le mieux marché ?"],
  },
  narrative_status: "generated",
  narrative_error: null,
  model: "gemini-pro",
  generated_by: "user",
  generated_at: "2026-10-11T08:00:05+00:00",
};

function renderView(value: Review, onDeleted = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewView review={value} onDeleted={onDeleted} />
    </QueryClientProvider>,
  );
  return onDeleted;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReviewView", () => {
  it("separates local facts from the assistant's reading", () => {
    renderView(review);
    expect(screen.getByRole("heading", { level: 2, name: "Semaine du 5 au 11 octobre 2026" })).toBeTruthy();

    const facts = screen.getByRole("region", { name: "Faits (calculés localement, sans IA)" });
    expect(within(facts).getByText("Rappeler la banque")).toBeTruthy();
    expect(within(facts).getByText("Déclarer les impôts")).toBeTruthy();
    expect(within(facts).getByText(/Période en cours/)).toBeTruthy();
    expect(within(facts).getByText("Conversations avec l'assistant")).toBeTruthy();
    expect(within(facts).getByRole("progressbar", { name: /Courir 100 km/ }).getAttribute("aria-valuenow")).toBe("42");
    expect(within(facts).getByRole("heading", { name: "Sleep" })).toBeTruthy();
    expect(within(facts).queryByText("Peut-être un manque de temps en fin de semaine")).toBeNull();

    const reading = screen.getByRole("region", { name: "Lecture de l'assistant" });
    expect(within(reading).getByRole("heading", { name: "Hypothèses — à confirmer ou infirmer" })).toBeTruthy();
    expect(within(reading).getByText("Bloquer 30 minutes pour les impôts")).toBeTruthy();
    expect(within(reading).getByText("Tâche en retard")).toBeTruthy();
  });

  it("explains why there is no reading", () => {
    renderView({ ...review, narrative: null, narrative_status: "unavailable", narrative_error: "llm_daily_quota_reached" });
    const reading = screen.getByRole("region", { name: "Lecture de l'assistant" });
    expect(within(reading).getByText(/quota gratuit du jour est atteint/)).toBeTruthy();
  });

  it("deletes only after confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const onDeleted = renderView(review);

    fireEvent.click(screen.getByRole("button", { name: /Supprimer le bilan « Semaine/ }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Supprimer le bilan" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("r1"));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/reviews/r1");
    expect(init.method).toBe("DELETE");
  });
});
