import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { DocumentDetail, DocumentSummary } from "@/lib/types";

import { DocumentDetailPanel } from "./DocumentDetail";
import { DocumentList } from "./DocumentList";

// Same shape as backend `service.detail` for a processed receipt.
const receipt: DocumentDetail = {
  id: "d1",
  space_id: "s1",
  title: "Ticket Carrefour",
  kind: "image",
  mime: "image/jpeg",
  size_bytes: 245_760,
  purpose: "receipt",
  status: "ready",
  error: null,
  sensitivity: ["bank"],
  text_source: "ocr",
  text_confidence: 0.55,
  pages: 1,
  processed_at: "2026-10-10T09:00:05Z",
  text_corrected_at: null,
  original_available: true,
  original_until: "2027-01-08T09:00:00Z",
  keep_original: false,
  source_type: null,
  source_id: null,
  analysis: {
    sensitivity_reasons: { bank: ["numéro de carte"] },
    receipt: {
      merchant: "CARREFOUR",
      tx_date: "2026-10-10",
      total: "1234.5",
      currency: "EUR",
      vat_rates: ["20"],
      card_payment: true,
      confidence: 0.8,
      warnings: ["Commerçant à vérifier."],
      lines: { merchant: "CARREFOUR MARKET", tx_date: null, total: "TOTAL TTC 1 234,50" },
    },
  },
  version: 3,
  created_at: "2026-10-10T09:00:00Z",
  updated_at: "2026-10-10T09:00:05Z",
  extracted_text: "CARREFOUR MARKET\nTOTAL TTC 1 234,50",
};

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

/** `responses` answers GET requests in turn (the last one repeats). */
function mockApi(responses: (DocumentDetail | { status: number; body: unknown })[] = [receipt]): Call[] {
  const calls: Call[] = [];
  let gets = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const method = init.method ?? "GET";
      const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ url, method, headers: init.headers as Record<string, string>, body });
      if (method === "DELETE") return new Response(null, { status: 204 });
      if (method === "PATCH") {
        const patched = { ...receipt, ...body, version: receipt.version + 1, text_corrected_at: "2026-10-12T10:00:00Z" };
        return new Response(JSON.stringify(patched), { status: 200 });
      }
      if (method === "POST") {
        return new Response(JSON.stringify({ ...receipt, status: "pending", version: 4 }), { status: 202 });
      }
      const answer = responses[Math.min(gets, responses.length - 1)];
      gets += 1;
      if (answer && "body" in answer && "status" in answer && typeof answer.status === "number") {
        return new Response(JSON.stringify(answer.body), { status: answer.status });
      }
      return new Response(JSON.stringify(answer), { status: 200 });
    }),
  );
  return calls;
}

function renderPanel(onClose: () => void = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Perso" }]}>
        <DocumentDetailPanel documentId="d1" onClose={onClose} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("DocumentDetailPanel", () => {
  it("shows what was read on a receipt, its sensitivity and the way to finances", async () => {
    mockApi();
    renderPanel();
    expect(await screen.findByRole("heading", { name: "Ticket Carrefour" })).toBeTruthy();

    // The DOM matcher turns non-breaking spaces into plain ones.
    expect(screen.getByText("Lu par OCR, confiance 55 %")).toBeTruthy();
    expect(screen.getByText("Vérifiez le texte, la lecture est incertaine.")).toBeTruthy();
    expect(screen.getByText("Image · 1 page")).toBeTruthy();

    expect(screen.getByText(/numéro de carte/)).toBeTruthy();
    expect(
      screen.getByText("Ce document est sensible : son contenu n'est jamais envoyé à l'assistant IA."),
    ).toBeTruthy();

    expect(screen.getByText("1 234,50 €")).toBeTruthy();
    expect(screen.getByText("CARREFOUR")).toBeTruthy();
    expect(screen.getByText("TOTAL TTC 1 234,50")).toBeTruthy();
    expect(screen.getByText("Commerçant à vérifier.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ajouter aux finances" }).getAttribute("href")).toBe(
      "/finances?receipt=d1",
    );

    const download = screen.getByRole("link", { name: "Télécharger l'original" });
    expect(download.getAttribute("href")).toBe("/v1/documents/d1/original");
    expect(download.hasAttribute("download")).toBe(true);
  });

  it("saves a text correction with the version read", async () => {
    const calls = mockApi();
    renderPanel();
    const text = await screen.findByLabelText("Texte du document");
    const save = screen.getByRole("button", { name: "Enregistrer la correction" });
    expect((save as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(text, { target: { value: "CARREFOUR MARKET\nTOTAL TTC 12,50" } });
    fireEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/v1/documents/d1");
    expect(patch?.headers["If-Match"]).toBe("3");
    expect(patch?.body).toEqual({ extracted_text: "CARREFOUR MARKET\nTOTAL TTC 12,50" });
    expect(await screen.findByText("Correction enregistrée.")).toBeTruthy();
    expect(screen.getByText(/^Corrigé le /)).toBeTruthy();
  });

  it("keeps the original indefinitely on request", async () => {
    const calls = mockApi();
    renderPanel();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Conserver l'original sans limite" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ keep_original: true });
  });

  it("polls while the document is being read, then allows corrections", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pending: DocumentDetail = {
      ...receipt,
      status: "processing",
      sensitivity: [],
      analysis: null,
      extracted_text: null,
      text_source: null,
      text_confidence: null,
      processed_at: null,
    };
    const calls = mockApi([pending, receipt]);
    renderPanel();
    expect(await screen.findByText(/^Lecture en cours sur le serveur/)).toBeTruthy();
    expect((screen.getByLabelText("Texte du document") as HTMLTextAreaElement).disabled).toBe(true);
    expect(screen.queryByRole("link", { name: "Ajouter aux finances" })).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100);
    });
    expect(await screen.findByRole("link", { name: "Ajouter aux finances" })).toBeTruthy();
    expect((screen.getByLabelText("Texte du document") as HTMLTextAreaElement).disabled).toBe(false);
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
  });

  it("explains a failure and lets the owner read the document again", async () => {
    const failed: DocumentDetail = {
      ...receipt,
      kind: "pdf",
      status: "failed",
      error: "encrypted",
      text_corrected_at: "2026-10-11T08:00:00Z",
    };
    const calls = mockApi([failed]);
    const confirm = vi.fn((_message?: string) => true);
    vi.stubGlobal("confirm", confirm);
    renderPanel();
    expect(await screen.findByText(/^PDF protégé par mot de passe/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Relire le document" }));
    expect(confirm.mock.calls[0]?.[0]).toMatch(/votre correction sera perdue/);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.url).toBe("/v1/documents/d1/reprocess");
  });

  it("deletes after confirmation", async () => {
    const calls = mockApi();
    const confirm = vi.fn((_message?: string) => true);
    vi.stubGlobal("confirm", confirm);
    const onClose = vi.fn();
    renderPanel(onClose);
    fireEvent.click(await screen.findByRole("button", { name: "Supprimer" }));
    expect(confirm.mock.calls[0]?.[0]).toMatch(/fichier original chiffré et le texte extrait seront effacés/);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/v1/documents/d1");
  });

  it("does nothing when the deletion is not confirmed", async () => {
    const calls = mockApi();
    vi.stubGlobal("confirm", vi.fn(() => false));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Supprimer" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("says so when the document no longer exists", async () => {
    mockApi([{ status: 404, body: { title: "Not found", status: 404, code: "document_not_found" } }]);
    renderPanel();
    expect(await screen.findByText("Ce document est introuvable ou a été supprimé.")).toBeTruthy();
  });
});

// The list leads to this panel: paging and filters are checked here too.
describe("DocumentList", () => {
  function summary(id: string, overrides: Partial<DocumentSummary> = {}): DocumentSummary {
    const { extracted_text: _text, ...base } = receipt;
    return { ...base, id, title: `Document ${id}`, ...overrides };
  }

  function mockList(): string[] {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        const page = url.includes("cursor=c2")
          ? { items: [summary("d3", { original_available: false, original_until: null })], next_cursor: null }
          : { items: [summary("d1"), summary("d2", { status: "failed", sensitivity: [] })], next_cursor: "c2" };
        return new Response(JSON.stringify(page), { status: 200 });
      }),
    );
    return urls;
  }

  function renderList() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Perso" }]}>
          <DocumentList selectedId="d2" />
        </SpaceProvider>
      </QueryClientProvider>,
    );
  }

  it("lists documents with their status, size and retention, and loads more", async () => {
    const urls = mockList();
    renderList();
    const first = await screen.findByRole("link", { name: /Document d1/ });
    expect(first.getAttribute("href")).toBe("/documents?id=d1");
    expect(first.textContent).toMatch(/Prêt/);
    expect(first.textContent).toMatch(/Banque/);
    expect(first.textContent).toMatch(/ · 240\sKo · Original conservé jusqu'au/);
    expect(screen.getByRole("link", { name: /Document d2/ }).getAttribute("aria-current")).toBe("true");
    expect(urls[0]).toBe("/v1/documents?limit=30");

    fireEvent.click(screen.getByRole("button", { name: "Charger plus" }));
    expect((await screen.findByRole("link", { name: /Document d3/ })).textContent).toMatch(/Original supprimé/);
    expect(urls[1]).toBe("/v1/documents?limit=30&cursor=c2");
    expect(screen.queryByRole("button", { name: "Charger plus" })).toBeNull();
  });

  it("filters by type and status", async () => {
    const urls = mockList();
    renderList();
    await screen.findByRole("link", { name: /Document d1/ });
    fireEvent.click(screen.getByRole("radio", { name: "Tickets" }));
    await waitFor(() => expect(urls).toContain("/v1/documents?purpose=receipt&limit=30"));
    fireEvent.change(screen.getByLabelText("Statut"), { target: { value: "failed" } });
    await waitFor(() => expect(urls).toContain("/v1/documents?purpose=receipt&status=failed&limit=30"));
  });
});
