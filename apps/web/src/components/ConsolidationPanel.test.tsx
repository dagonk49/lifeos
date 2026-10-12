import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Approval, ConversationDetail } from "@/lib/types";

import { ConsolidationPanel } from "./ConsolidationPanel";

const conversation: ConversationDetail = {
  id: "c1",
  space_id: "s1",
  title: "Vacances",
  last_message_at: "2026-10-11T09:00:00Z",
  consolidated_until: null,
  created_at: "2026-10-11T08:00:00Z",
  messages: [
    {
      id: "m1",
      conversation_id: "c1",
      role: "user",
      content: "J'ai décidé de partir en Bretagne en juillet.",
      status: "ok",
      model: null,
      agent: null,
      purge_after: "2027-01-09T08:00:00Z",
      retained: false,
      created_at: "2026-10-11T08:00:00Z",
    },
  ],
};

const approval: Approval = {
  id: "a1",
  space_id: "s1",
  action: "chat.apply_proposals",
  description: "Enregistrer les tâches et faits tirés d'une conversation",
  params: {
    conversation_id: "c1",
    until: "2026-10-11T08:00:00Z",
    tasks: [],
    facts: [{ kind: "decision", statement: "Vacances en Bretagne en juillet", occurred_on: null }],
  },
  preview: { summary: "0 tâche(s), 1 fait(s) à enregistrer", items: [] },
  level: "approval",
  status: "pending",
  requested_by: "agent:core",
  created_at: "2026-10-11T09:00:00Z",
  expires_at: "2026-10-18T09:00:00Z",
  decided_at: null,
  executed_at: null,
  result: null,
  error: null,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPanel(detail: ConversationDetail = conversation) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConsolidationPanel conversation={detail} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ConsolidationPanel", () => {
  it("explains the analysis and shows the proposals to review", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith("/consolidate")
        ? json(200, { approval_id: "a1", messages: 1, proposed: 1 })
        : json(200, approval),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();

    expect(screen.getByText(/Pas encore consolidée/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "Retenir l'essentiel" });
    expect(button.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByText(/une seule requête Gemini/)).toBeTruthy();
    fireEvent.click(button);

    expect(await screen.findByText(/1 proposition tirée de 1 message/)).toBeTruthy();
    expect(await screen.findByText(approval.description)).toBeTruthy();
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/v1/chat/conversations/c1/consolidate",
      "/v1/approvals/a1",
    ]);
    expect(screen.getByRole("link", { name: "Voir toutes les approbations" }).getAttribute("href")).toBe("/approvals");
  });

  it("says when nothing durable was found", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { approval_id: null, messages: 1, proposed: 0 })));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Retenir l'essentiel" }));
    expect(await screen.findByText("Rien de durable à retenir dans les nouveaux messages.")).toBeTruthy();
  });

  it("shows the conflict message when there is nothing new", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(409, { title: "Conflict", status: 409, code: "nothing_to_consolidate" })),
    );
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Retenir l'essentiel" }));
    expect(await screen.findByText("Aucun nouveau message à consolider dans cette conversation.")).toBeTruthy();
  });

  it("is disabled once everything is consolidated", () => {
    renderPanel({ ...conversation, consolidated_until: "2026-10-11T08:00:00Z" });
    expect(screen.getByText(/Consolidée jusqu'au/)).toBeTruthy();
    expect(screen.getByText(/Aucun nouveau message à analyser/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Retenir l'essentiel" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
