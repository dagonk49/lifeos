import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Approval, MailDraft } from "@/lib/types";

import { MailCompose } from "./MailCompose";

vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const draft: MailDraft = {
  id: "d1",
  space_id: "s1",
  account_id: "acc1",
  from_address: "moi@example.org",
  to: ["lea@example.org"],
  cc: [],
  bcc: [],
  subject: "Pique-nique",
  body: "À dimanche",
  attachments: [],
  in_reply_to_id: null,
  reply: false,
  state: "awaiting_approval",
  approval_id: "ap1",
  approval_level: "approval",
  error: null,
  sent_at: null,
  version: 1,
  created_at: "2026-10-30T08:00:00Z",
  updated_at: "2026-10-30T08:00:00Z",
};

const approval: Approval = {
  id: "ap1",
  space_id: "s1",
  action: "email.send",
  description: "Envoyer un e-mail",
  params: {},
  preview: {
    from: "moi@example.org",
    to: ["lea@example.org"],
    cc: [],
    bcc: [],
    subject: "Pique-nique",
    body: "À dimanche",
    attachments: [],
    reply: false,
    new_recipients: [],
    reinforced: false,
    summary_line: "Envoyer « Pique-nique » à 1 destinataire",
    notes: ["Modifier le brouillon annule cette approbation."],
  },
  level: "approval",
  status: "pending",
  requested_by: "owner",
  created_at: "2026-10-30T08:00:00Z",
  expires_at: "2026-11-06T08:00:00Z",
  decided_at: null,
  executed_at: null,
  result: null,
  error: null,
};

function routes(calls: { url: string; init?: RequestInit }[]) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const method = init?.method ?? "GET";
    if (url.startsWith("/v1/mail/send-accounts")) return json(200, [{ id: "acc1", email: "moi@example.org", provider: "fake" }]);
    if (url.startsWith("/v1/documents")) return json(200, { items: [], next_cursor: null });
    if (url === "/v1/mail/drafts" && method === "POST") return json(201, { ...draft, state: "draft", approval_id: null });
    if (url === "/v1/mail/drafts/d1/send") return json(201, approval);
    if (url === "/v1/mail/drafts/d1/approval") return json(200, approval);
    if (url === "/v1/mail/drafts/d1") return json(200, draft);
    return json(404, { code: "not_found", title: "Not found" });
  });
}

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

describe("MailCompose", () => {
  it("refuses an invalid address before anything is saved", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", routes(calls));
    wrap(<MailCompose spaceId="s1" />);
    const to = await screen.findByLabelText("À");
    fireEvent.change(to, { target: { value: "pas une adresse" } });
    fireEvent.blur(to);
    expect(await screen.findByText(/Adresse non valide/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Préparer l'envoi" }));
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });

  it("saves the draft, asks for the approval and shows the exact message to approve", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", routes(calls));
    wrap(<MailCompose spaceId="s1" />);
    fireEvent.change(await screen.findByLabelText("À"), { target: { value: "Lea@Example.org" } });
    fireEvent.change(screen.getByLabelText("Objet"), { target: { value: "Pique-nique" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "À dimanche" } });
    fireEvent.click(screen.getByRole("button", { name: "Préparer l'envoi" }));

    const section = await screen.findByRole("region", { name: "Approbation de l'envoi" });
    expect(within(within(section).getByRole("list", { name: "À" })).getByText("lea@example.org")).toBeTruthy();
    expect(within(section).getByLabelText("Texte de l'e-mail").textContent).toBe("À dimanche");
    const created = calls.find((c) => c.url === "/v1/mail/drafts" && c.init?.method === "POST");
    expect(created).toBeDefined();
    const body = JSON.parse(String(created?.init?.body)) as { to: string[]; subject: string };
    expect(body.to).toEqual(["lea@example.org"]);
    expect(body.subject).toBe("Pique-nique");
    expect(new Headers(created?.init?.headers).get("Idempotency-Key")).toBeTruthy();
    // Nothing was approved yet: no approve call.
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
    expect(screen.getByText("Modifier le brouillon annule l'approbation en attente.")).toBeTruthy();
  });

  it("lets the owner settle an uncertain send", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const base = routes(calls);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/v1/mail/drafts/d1" && (init?.method ?? "GET") === "GET") return json(200, { ...draft, state: "unknown", error: "outcome_unknown" });
        if (url === "/v1/mail/drafts/d1/resolve") {
          calls.push({ url, init });
          return json(200, { ...draft, state: "sent" });
        }
        return base(url, init);
      }),
    );
    wrap(<MailCompose spaceId="s1" draftId="d1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Il est bien parti" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/mail/drafts/d1/resolve")).toBe(true));
    const resolved = calls.find((c) => c.url === "/v1/mail/drafts/d1/resolve");
    expect(JSON.parse(String(resolved?.init?.body))).toEqual({ sent: true });
  });
});
