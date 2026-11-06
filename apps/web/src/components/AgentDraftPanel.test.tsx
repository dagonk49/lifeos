import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentDraftResult, MailDraft } from "@/lib/types";

import { AgentDraftPanel } from "./AgentDraftPanel";
import { MailCompose } from "./MailCompose";

vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// What a model could write after reading a hostile e-mail: markup, an unknown address.
const HOSTILE_BODY = '<img src="https://evil.example/x.png"> **Voici** tous les e-mails.';

const draft: MailDraft = {
  id: "d9",
  space_id: "s1",
  account_id: "acc1",
  from_address: "moi@example.org",
  to: ["lea@example.org", "x@evil.example"],
  cc: [],
  bcc: [],
  subject: "Re: Facture",
  body: HOSTILE_BODY,
  attachments: [],
  in_reply_to_id: "m1",
  reply: true,
  state: "draft",
  approval_id: null,
  approval_level: null,
  error: null,
  sent_at: null,
  author_agent: "core",
  conversation_id: "c1",
  owner_edited: false,
  version: 1,
  created_at: "2026-11-04T08:00:00Z",
  updated_at: "2026-11-04T08:00:00Z",
};

const result: AgentDraftResult = { draft, dropped_recipients: 1 };

function wrap(node: React.ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {node}
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentDraftPanel", () => {
  it("asks the agent for a draft and shows it as plain text, never sending it", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url === "/v1/chat/conversations/c1/mail-draft") return json(201, result);
        return json(404, { code: "not_found", title: "Not found" });
      }),
    );
    wrap(<AgentDraftPanel conversationId="c1" replyToId="m1" label="Proposer une réponse" />);
    fireEvent.click(screen.getByRole("button", { name: "Proposer une réponse" }));

    const card = await screen.findByRole("article", { name: "Brouillon proposé par l'agent Assistant général" });
    expect(within(card).getByText("Brouillon proposé par l'agent Assistant général")).toBeTruthy();
    // Every recipient is shown, the unknown one included; the dropped one is said.
    expect(within(card).getByText("lea@example.org, x@evil.example")).toBeTruthy();
    expect(within(card).getByText(/Une adresse proposée n'était pas valide/)).toBeTruthy();
    expect(within(card).getByText(/demande votre passkey/)).toBeTruthy();
    // The model's text stays text: no image, no interpreted Markdown.
    expect(within(card).getByText(HOSTILE_BODY)).toBeTruthy();
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector("strong")).toBeNull();
    expect(within(card).getByRole("link", { name: "Vérifier et préparer l'envoi" }).getAttribute("href")).toBe(
      "/mail/compose?draft=d9",
    );

    // One request, to the drafting endpoint only: nothing about sending or approving.
    expect(calls).toHaveLength(1);
    const body = JSON.parse(String(calls[0]?.init?.body));
    expect(body).toEqual({ agent: "core", in_reply_to_id: "m1" });
    expect(new Headers(calls[0]?.init?.headers).get("Idempotency-Key")).toBeTruthy();

    // A new version of the same draft, while the owner has not touched it.
    fireEvent.click(screen.getByRole("button", { name: "Proposer une autre version" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({ agent: "core", draft_id: "d9" });
    expect(calls.every((c) => !c.url.includes("/send") && !c.url.includes("/approve"))).toBe(true);
  });

  it("explains a refusal: an agent without the e-mail permission, a draft the owner took", async () => {
    const replies = [
      json(403, { code: "agent_permission_required", title: "No permission", resource: "email" }),
      json(409, { code: "draft_not_editable_by_agent", title: "Only the owner can change this draft now" }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => replies.shift() ?? json(500, { code: "x", title: "x" })),
    );
    wrap(<AgentDraftPanel conversationId="c1" agent="projects" />);
    expect(screen.getByText(/Rédigé par l'agent Projets/)).toBeTruthy();
    const ask = screen.getByRole("button", { name: "Proposer un brouillon d'e-mail" });
    fireEvent.click(ask);
    expect(await screen.findByText(/n'a pas la permission « email » en écriture/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Réglages > Permissions des agents" })).toBeTruthy();
    await waitFor(() => expect((ask as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ask);
    expect(await screen.findByText(/Ce brouillon est désormais le vôtre/)).toBeTruthy();
  });
});

describe("MailCompose with an agent's draft", () => {
  it("says which agent proposed it and that only the owner sends it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/v1/mail/send-accounts")) return json(200, [{ id: "acc1", email: "moi@example.org", provider: "fake" }]);
        if (url.startsWith("/v1/documents")) return json(200, { items: [], next_cursor: null });
        if (url === "/v1/mail/drafts/d9") return json(200, { ...draft, author_agent: "projects" });
        return json(404, { code: "not_found", title: "Not found" });
      }),
    );
    wrap(<MailCompose spaceId="s1" draftId="d9" />);
    expect(await screen.findByText("Brouillon proposé par l'agent Projets")).toBeTruthy();
    expect(screen.getByText(/l'agent ne pourra plus le changer/)).toBeTruthy();
    // The model's text sits in a plain text field, as written.
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(HOSTILE_BODY);
    expect((screen.getByLabelText("À") as HTMLInputElement).value).toBe("lea@example.org, x@evil.example");
    expect(screen.getByRole("button", { name: "Préparer l'envoi" })).toBeTruthy();
  });
});
