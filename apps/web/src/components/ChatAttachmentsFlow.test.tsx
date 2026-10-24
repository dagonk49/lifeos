import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "@/app/(app)/chat/page";
import { SpaceProvider } from "@/lib/space";
import type { ConversationDetail, DocumentSummary, SendPreview, SendResult } from "@/lib/types";

vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

const detail: ConversationDetail = {
  id: "c1",
  space_id: "s1",
  title: "Courrier",
  last_message_at: "2026-10-11T08:00:05Z",
  consolidated_until: null,
  created_at: "2026-10-11T08:00:00Z",
  messages: [],
};

function document(id: string, overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id,
    space_id: "s1",
    title: `Doc ${id}`,
    kind: "pdf",
    mime: "application/pdf",
    size_bytes: 1200,
    purpose: "attachment",
    status: "ready",
    error: null,
    sensitivity: [],
    text_source: "pdf",
    text_confidence: null,
    pages: 1,
    processed_at: "2026-10-11T08:00:00Z",
    text_corrected_at: null,
    original_available: true,
    original_downloadable: true,
    original_until: null,
    keep_original: false,
    local_only: false,
    source_type: null,
    source_id: null,
    analysis: null,
    version: 1,
    created_at: "2026-10-11T08:00:00Z",
    updated_at: "2026-10-11T08:00:00Z",
    ...overrides,
  };
}

const reply: SendResult = {
  user_message: {
    id: "u1",
    conversation_id: "c1",
    role: "user",
    content: "Résume",
    status: "ok",
    model: null,
    agent: null,
    purge_after: null,
    retained: false,
    created_at: "2026-10-11T08:01:00Z",
  },
  assistant_message: null,
  error: null,
  context_items: 0,
  agent: "core",
  suggested_agent: null,
};

type Call = { url: string; method: string; body: unknown };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type PreviewBody = { space_id: string; content: string; agent: string; attachments: string[] };

/** The server's control before sending; by default every attachment goes, nothing masked. */
function sentAsIs(body: PreviewBody): SendPreview {
  return {
    masked: {},
    attachments: body.attachments.map((id) => ({
      document_id: id,
      sent: true,
      reason: null,
      sensitivity: [],
      masked: {},
    })),
  };
}

/** `finishUpload` resolves the pending POST /v1/documents. */
function mockApi(
  documents: Record<string, DocumentSummary | null>,
  preview: (body: PreviewBody) => SendPreview = sentAsIs,
) {
  const calls: Call[] = [];
  let finishUpload: (doc: DocumentSummary) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ url, method, body });
      if (url === "/v1/chat/conversations" && method === "GET") return json({ items: [detail], next_cursor: null });
      if (url === "/v1/chat/conversations/c1") return json(detail);
      if (url.startsWith("/v1/agents?")) return json([]);
      if (url === "/v1/voice/status") return json({ transcription: false, max_seconds: 900 });
      if (url === "/v1/documents" && method === "POST") {
        return new Promise<Response>((resolve) => {
          finishUpload = (doc) => resolve(json(doc, 201));
        });
      }
      if (url.startsWith("/v1/documents/")) {
        const doc = documents[url.split("/").pop() ?? ""];
        return doc ? json(doc) : json({ title: "Not found", status: 404, code: "document_not_found" }, 404);
      }
      if (url === "/v1/chat/conversations/c1/messages") return json(reply, 201);
      if (url === "/v1/chat/preview") return json(preview(body as PreviewBody));
      return json({ title: "Not found", status: 404, code: "not_found" }, 404);
    }),
  );
  return { calls, finishUpload: (doc: DocumentSummary) => finishUpload(doc) };
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Personnel" }]}>
        <ChatPage />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Assistant page: attachments", () => {
  it("holds the message while a file is uploading, then sends it with the attachment", async () => {
    const { calls, finishUpload } = mockApi({ d1: document("d1") });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    const input = document_input();
    fireEvent.change(input, { target: { files: [new File(["%PDF-1.7"], "lettre.pdf", { type: "application/pdf" })] } });
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Résume" } });
    const send = await screen.findByRole("button", { name: "Envoi de la pièce jointe…" });
    expect(send).toHaveProperty("disabled", true);

    finishUpload(document("d1"));
    await screen.findByText("Texte prêt");
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith("/messages") && c.method === "POST")?.body).toMatchObject({
        attachments: ["d1"],
      }),
    );
  });

  it("leaves out documents deleted meanwhile or whose reading is postponed, without waiting", async () => {
    const { calls, finishUpload } = mockApi({
      late: document("late", { status: "pending", error: "extraction_unavailable" }),
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    fireEvent.change(document_input(), { target: { files: [new File(["x"], "a.pdf")] } });
    await uploadStarted(calls);
    finishUpload(document("late", { status: "pending", error: "extraction_unavailable" }));
    await screen.findByText(/Lecture reportée/);
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Résume" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    // Left out: said before sending, then sent without it once confirmed.
    const review = await screen.findByRole("region", { name: "Avant l'envoi à l'assistant IA" });
    expect(review.textContent).toContain("« Doc late » : reste sur votre serveur (lecture pas encore terminée)");
    fireEvent.click(screen.getByRole("button", { name: "Confirmer l'envoi" }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith("/messages") && c.method === "POST")?.body).toMatchObject({
        attachments: [],
      }),
    );
  });

  it("sends at once when nothing is masked or left out", async () => {
    const { calls } = mockApi({});
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Bonjour" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/messages") && c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.url === "/v1/chat/preview")?.body).toEqual({
      space_id: "s1",
      content: "Bonjour",
      agent: "core",
      attachments: [],
    });
    expect(screen.queryByRole("region", { name: "Avant l'envoi à l'assistant IA" })).toBeNull();
  });

  it("asks before sending masked identifiers; « Annuler » keeps the message and sends nothing", async () => {
    const { calls } = mockApi({}, () => ({ masked: { iban: 1, phone: 2 }, attachments: [] }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Mon IBAN et mes numéros" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    const review = await screen.findByRole("region", { name: "Avant l'envoi à l'assistant IA" });
    expect(review.textContent).toContain(
      "Dans votre message : 1 IBAN, 2 numéros de téléphone seront remplacés par une étiquette",
    );
    fireEvent.click(screen.getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("region", { name: "Avant l'envoi à l'assistant IA" })).toBeNull();
    expect(screen.getByLabelText<HTMLTextAreaElement>("Votre message").value).toBe("Mon IBAN et mes numéros");
    expect(calls.some((c) => c.url.endsWith("/messages") && c.method === "POST")).toBe(false);
  });

  it("leaves out a document the server keeps local, and lets the owner remove it", async () => {
    const { calls, finishUpload } = mockApi({ d1: document("d1") }, (body) => ({
      masked: {},
      attachments: body.attachments.map((id) => ({
        document_id: id,
        sent: false,
        reason: "sensitive",
        sensitivity: ["bank"],
        masked: {},
      })),
    }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    fireEvent.change(document_input(), { target: { files: [new File(["x"], "a.pdf")] } });
    await uploadStarted(calls);
    finishUpload(document("d1"));
    await screen.findByText("Texte prêt");
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Résume" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    const review = await screen.findByRole("region", { name: "Avant l'envoi à l'assistant IA" });
    // Checked with the current rules on the server: sensitive, although stored as not.
    expect(review.textContent).toContain("« Doc d1 » : reste sur votre serveur (document sensible (banque))");
    fireEvent.click(screen.getByRole("button", { name: "Retirer « Doc d1 » du message" }));
    expect(review.textContent).toContain("Plus rien n'est masqué ni laissé de côté.");
    expect(screen.queryByText("Texte prêt")).toBeNull(); // gone from the message too
    fireEvent.click(screen.getByRole("button", { name: "Confirmer l'envoi" }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith("/messages") && c.method === "POST")?.body).toMatchObject({
        attachments: [],
      }),
    );
  });

  it("sends nothing when the control fails", async () => {
    const { calls } = mockApi({}, () => {
      throw new Error("down");
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Bonjour" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(await screen.findByText(/Contrôle avant l'envoi impossible, rien n'a été envoyé/)).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/messages") && c.method === "POST")).toBe(false);
  });

  it("opens « Vérifier le texte » in a new tab, keeping the message being written", async () => {
    const { calls, finishUpload } = mockApi({ d1: document("d1") });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Courrier/ }));
    fireEvent.change(document_input(), { target: { files: [new File(["x"], "a.pdf")] } });
    await uploadStarted(calls);
    finishUpload(document("d1"));
    const link = await screen.findByRole("link", { name: /Vérifier le texte/ });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });
});

async function uploadStarted(calls: Call[]) {
  await waitFor(() => expect(calls.some((c) => c.url === "/v1/documents" && c.method === "POST")).toBe(true));
}

function document_input(): HTMLInputElement {
  const input = window.document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("no file input");
  return input;
}
