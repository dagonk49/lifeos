import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { REVIEW_DONE_MESSAGES, mergeReviewQueues, type SpacedReviewItem } from "@/lib/memoryReview";
import type { Fact, MemoryReviewItem } from "@/lib/types";

import { MemoryReviewItemCard } from "./MemoryReviewItem";

function fact(id: string, statement: string): Fact {
  return {
    id,
    space_id: "s1",
    kind: "preference",
    statement,
    origin: "extracted",
    confidence: "0.80",
    status: "active",
    occurred_at: null,
    source_type: "journal",
    source_id: null,
    supersedes_id: null,
    last_confirmed_at: null,
    created_at: "2026-01-10T08:00:00Z",
    updated_at: "2026-01-10T08:00:00Z",
  };
}

function spaced(item: MemoryReviewItem): SpacedReviewItem {
  const [first] = mergeReviewQueues([
    { spaceId: "s1", spaceName: "Personnel", queue: { items: [item], semantic: true } },
  ]).items;
  if (!first) throw new Error("no item");
  return first;
}

const conflict = spaced({
  kind: "conflict",
  reason: "Formulations opposées : laquelle est vraie aujourd'hui ?",
  facts: [fact("a", "J'aime le café"), fact("b", "Je n'aime pas le café")],
  distance: 0.12,
});

function renderCard(item: SpacedReviewItem, onDone = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryReviewItemCard item={item} showSpace onDone={onDone} />
    </QueryClientProvider>,
  );
  return onDone;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MemoryReviewItemCard", () => {
  it("keeps both facts of a pair", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const onDone = renderCard(conflict);

    expect(screen.getByRole("heading", { name: "Contradiction possible" })).toBeTruthy();
    expect(screen.getByText("Personnel")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Garder les deux" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(REVIEW_DONE_MESSAGES.dismiss));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/memory/review/dismiss");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ fact_ids: ["a", "b"] });
  });

  it("retracts the chosen fact only", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(fact("b", "Je n'aime pas le café")), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onDone = renderCard(conflict);

    fireEvent.click(screen.getByRole("button", { name: "Retirer « Je n'aime pas le café »" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(REVIEW_DONE_MESSAGES.retract));
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/v1/memory/facts/b/retract");
  });

  it("confirms an old preference and shows the API error otherwise", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ title: "Conflict", status: 409, code: "fact_not_active" }), { status: 409 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onDone = renderCard(
      spaced({
        kind: "stale",
        reason: "Préférence ancienne : est-elle toujours vraie ?",
        facts: [fact("p", "Je préfère le thé")],
        distance: null,
      }),
    );

    expect(screen.queryByRole("button", { name: "Garder les deux" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Toujours vrai : « Je préfère le thé »" }));

    expect(await screen.findByText("Ce fait n'est plus actif.")).toBeTruthy();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/v1/memory/facts/p/confirm");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("corrects a fact inline", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(fact("c", "Je bois du café le matin")), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    const onDone = renderCard(conflict);

    fireEvent.click(screen.getByRole("button", { name: "Corriger « J'aime le café »" }));
    fireEvent.change(screen.getByLabelText("Énoncé corrigé"), { target: { value: "Je bois du café le matin" } });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer la correction" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(REVIEW_DONE_MESSAGES.correct));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/memory/facts/a/correct");
    expect(JSON.parse(String(init.body))).toEqual({ statement: "Je bois du café le matin" });
  });
});
