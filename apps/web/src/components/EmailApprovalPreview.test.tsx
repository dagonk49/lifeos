import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Approval } from "@/lib/types";

import { ApprovalReview } from "./ApprovalReview";

const passkey = vi.hoisted(() => ({ login: vi.fn(async () => ({})) }));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: passkey.login }));

const email: Approval = {
  id: "ap1",
  space_id: "s1",
  action: "email.send_reinforced",
  description: "Envoyer un e-mail à un nouveau destinataire ou avec une pièce jointe sensible",
  params: {},
  preview: {
    from: "moi@example.org",
    to: ["lea@example.org"],
    cc: ["sam@example.org"],
    bcc: ["archive@example.org"],
    subject: "Réunion d'été",
    body: "Bonjour Léa,\n\n<b>pas du HTML</b>\nÀ bientôt",
    attachments: [
      {
        title: "Bulletin",
        filename: "Bulletin.pdf",
        mime: "application/pdf",
        size_bytes: 2048,
        sensitivity: ["bank"],
        local_only: false,
      },
    ],
    reply: false,
    new_recipients: ["sam@example.org"],
    reinforced: true,
    summary_line: "Envoyer « Réunion d'été » à 3 destinataires",
    notes: ["Modifier le brouillon annule cette approbation."],
  },
  level: "reinforced",
  status: "pending",
  requested_by: "owner",
  created_at: "2026-10-30T08:00:00Z",
  expires_at: "2026-11-06T08:00:00Z",
  decided_at: null,
  executed_at: null,
  result: null,
  error: null,
};

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
  passkey.login.mockClear();
});

describe("EmailApprovalPreview", () => {
  it("shows exactly the recipients, the subject, the whole body and the attachments", () => {
    wrap(<ApprovalReview approval={email} />);
    expect(within(screen.getByRole("list", { name: "À" })).getByText("lea@example.org")).toBeTruthy();
    const cc = screen.getByRole("list", { name: "Cc" });
    expect(within(cc).getByText("sam@example.org")).toBeTruthy();
    expect(within(cc).getByText("Nouveau destinataire")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Cci" })).getByText("archive@example.org")).toBeTruthy();
    expect(screen.getByText("moi@example.org")).toBeTruthy();
    expect(screen.getByText("Réunion d'été")).toBeTruthy();
    // Plain text, never markup (T14).
    expect(screen.getByLabelText("Texte de l'e-mail").textContent).toBe(
      "Bonjour Léa,\n\n<b>pas du HTML</b>\nÀ bientôt",
    );
    const attachments = screen.getByRole("list", { name: "Pièces jointes" });
    expect(within(attachments).getByText("Bulletin.pdf")).toBeTruthy();
    expect(within(attachments).getByText("2 Ko")).toBeTruthy();
    expect(screen.getByText("Modifier le brouillon annule cette approbation.")).toBeTruthy();
    expect(screen.getByText("Confirmation par passkey")).toBeTruthy();
  });

  it("approves once: the button is disabled after the first click", async () => {
    let resolve: (value: Response) => void = () => {};
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((r) => {
          resolve = r;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ApprovalReview approval={{ ...email, action: "email.send", level: "approval" }} />);
    const button = screen.getByRole("button", { name: "Approuver" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
    resolve(new Response(JSON.stringify({ ...email, status: "executed" }), { status: 200 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    // Still disabled once the answer came back (the list refreshes meanwhile).
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("says when an e-mail left, and when it is uncertain", () => {
    wrap(<ApprovalReview approval={{ ...email, status: "executed" }} />);
    expect(screen.getAllByText("Envoyé").length).toBeGreaterThan(0);
    cleanup();
    wrap(<ApprovalReview approval={{ ...email, status: "failed", error: "outcome_unknown" }} />);
    expect(screen.getByText("État incertain")).toBeTruthy();
    expect(screen.getByText(/vérifiez vos e-mails envoyés/)).toBeTruthy();
    cleanup();
    wrap(<ApprovalReview approval={{ ...email, status: "invalidated", error: "draft_changed" }} />);
    expect(screen.getByText("Invalidée")).toBeTruthy();
    expect(screen.getByText(/Le brouillon a été modifié/)).toBeTruthy();
  });

  it("asks for the passkey before a reinforced approval", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: "reauthentication_required", title: "t" }), { status: 403 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...email, status: "executed" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ApprovalReview approval={email} />);
    fireEvent.click(screen.getByRole("button", { name: "Approuver avec ma passkey" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(passkey.login).toHaveBeenCalledOnce();
  });

  it("shows a redacted approval as a summary only", () => {
    wrap(
      <ApprovalReview
        approval={{
          ...email,
          status: "executed",
          preview: { redacted: true, summary_line: "Contenu effacé après la durée de conservation", recipients: 3 },
        }}
      />,
    );
    expect(screen.getByText(/Contenu effacé après la durée de conservation \(3 destinataires\)/)).toBeTruthy();
  });
});
