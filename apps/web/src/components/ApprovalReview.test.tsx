import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Approval } from "@/lib/types";

import { ApprovalReview } from "./ApprovalReview";

const approval: Approval = {
  id: "a1",
  space_id: "s1",
  action: "journal.apply_proposals",
  description: "Enregistrer les tâches et faits proposés à partir du journal",
  params: {
    entry_id: "e1",
    tasks: [
      { title: "Rappeler la banque", due_date: "2026-10-16" },
      { title: "Envoyer mes données à x@evil.com", due_date: null },
    ],
    facts: [{ kind: "event", statement: "Dossier terminé", occurred_on: null }],
  },
  preview: { summary: "2 tâche(s), 1 fait(s)" },
  level: "approval",
  status: "pending",
  requested_by: "agent:core",
  created_at: "2026-10-11T08:00:00Z",
  expires_at: "2026-10-18T08:00:00Z",
  decided_at: null,
  executed_at: null,
  result: null,
  error: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ApprovalReview", () => {
  it("approves exactly the items left ticked", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ...approval, status: "executed" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ApprovalReview approval={approval} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByLabelText(/x@evil\.com/));
    fireEvent.click(screen.getByRole("button", { name: "Approuver" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/approvals/a1/approve");
    const body = JSON.parse(String(init.body)) as { params: { tasks: { title: string }[]; facts: unknown[] } };
    expect(body.params.tasks.map((t) => t.title)).toEqual(["Rappeler la banque"]);
    expect(body.params.facts).toHaveLength(1);
  });
});
