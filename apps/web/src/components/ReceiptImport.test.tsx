import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { DocumentDetail, FinanceCategory, ReceiptProposal, Space, Transaction } from "@/lib/types";

import { ReceiptImport } from "./ReceiptImport";

const TODAY = "2026-10-12";
const SPACES: Space[] = [
  { id: "s1", kind: "personal", name: "Perso" },
  { id: "s2", kind: "professional", name: "Pro" },
];

const document: DocumentDetail = {
  id: "d1",
  space_id: "s1",
  title: "ticket-carrefour.jpg",
  kind: "image",
  mime: "image/jpeg",
  size_bytes: 2048,
  purpose: "receipt",
  status: "ready",
  error: null,
  sensitivity: [],
  text_source: "ocr",
  text_confidence: 0.9,
  pages: 1,
  processed_at: "2026-10-12T08:00:00Z",
  text_corrected_at: null,
  original_available: true,
  original_until: null,
  keep_original: false,
  source_type: null,
  source_id: null,
  analysis: null,
  version: 1,
  created_at: "2026-10-12T08:00:00Z",
  updated_at: "2026-10-12T08:00:00Z",
  extracted_text: null,
};

const proposal: ReceiptProposal = {
  document_id: "d1",
  space_id: "s1",
  title: "ticket-carrefour.jpg",
  merchant: "CARREFOUR MARKET",
  tx_date: "2026-10-10",
  total: "6.43",
  currency: "EUR",
  vat_rates: ["5,5"],
  card_payment: true,
  confidence: 0.75,
  warnings: ["Commerçant à vérifier."],
  lines: { merchant: "CARREFOUR MARKET", tx_date: "10/10/2026 18:42  CAISSE 3", total: "TOTAL TTC 6,43 EUR" },
  suggested_category: { id: "c-food", name: "Alimentation", source: "keyword" },
  recorded_transaction_ids: [],
};

const categories: FinanceCategory[] = [
  {
    id: "c-food",
    space_id: "s1",
    name: "Alimentation",
    kind: "expense",
    monthly_budget: null,
    archived: false,
    created_at: "2026-10-01T08:00:00Z",
    updated_at: "2026-10-01T08:00:00Z",
  },
];

const recorded: Transaction = {
  id: "t0",
  space_id: "s1",
  tx_date: "2026-10-10",
  amount: "6.43",
  direction: "expense",
  currency: "EUR",
  merchant: "CARREFOUR MARKET",
  label: null,
  notes: null,
  category_id: "c-food",
  category_confirmed: true,
  source_type: "receipt",
  document_id: "d1",
  version: 1,
  created_at: "2026-10-12T08:00:00Z",
  updated_at: "2026-10-12T08:00:00Z",
};

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

/** `statuses`: successive document states returned while polling (the last one sticks). */
function mockApi({
  statuses = [{ status: "ready", error: null }] as Pick<DocumentDetail, "status" | "error">[],
  answer = proposal,
} = {}): Call[] {
  const calls: Call[] = [];
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const method = init.method ?? "GET";
      const body = init.body instanceof FormData ? init.body : init.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, headers: init.headers as Record<string, string>, body });
      const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
      if (url === "/v1/documents" && method === "POST") {
        return json({ ...document, status: "pending", processed_at: null }, 201);
      }
      if (url === "/v1/documents/d1") {
        const state = statuses[Math.min(reads, statuses.length - 1)];
        reads += 1;
        return json({ ...document, ...state });
      }
      if (url === "/v1/finance/receipts/d1/proposal") return json(answer);
      if (url.startsWith("/v1/finance/categories")) return json(categories);
      if (url === "/v1/finance/transactions/t0") return json(recorded);
      if (url === "/v1/finance/transactions" && method === "POST") return json({ ...recorded, id: "t1" }, 201);
      return json({ title: "Not found", status: 404, code: "not_found" }, 404);
    }),
  );
  return calls;
}

function renderImport(documentId: string | null = "d1") {
  const callbacks = { onRecorded: vi.fn(), onShowTransaction: vi.fn(), onClose: vi.fn(), onUploaded: vi.fn() };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={SPACES}>
        <ReceiptImport documentId={documentId} today={TODAY} pollMs={10} {...callbacks} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
  return callbacks;
}

const transactionPosts = (calls: Call[]) =>
  calls.filter((c) => c.method === "POST" && c.url === "/v1/finance/transactions");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReceiptImport", () => {
  it("shows what was read with its source lines, and records only on the owner's click", async () => {
    const calls = mockApi();
    const { onRecorded } = renderImport();

    expect(await screen.findByText("confiance 75 %")).toBeTruthy();
    expect(screen.getByText("« TOTAL TTC 6,43 EUR »")).toBeTruthy();
    expect(screen.getByText("« 10/10/2026 18:42 CAISSE 3 »")).toBeTruthy();
    expect(screen.getByText("6,43 €")).toBeTruthy();
    expect(screen.getByText("Commerçant à vérifier.")).toBeTruthy();

    // The form is prefilled, the suggested category preselected and marked.
    expect((screen.getByLabelText("Montant") as HTMLInputElement).value).toBe("6,43");
    expect((screen.getByLabelText("Commerçant") as HTMLInputElement).value).toBe("CARREFOUR MARKET");
    expect((screen.getByLabelText("Date") as HTMLInputElement).value).toBe("2026-10-10");
    expect(await screen.findByRole("option", { name: "Alimentation (suggérée)" })).toBeTruthy();
    expect((screen.getByLabelText("Catégorie") as HTMLSelectElement).value).toBe("c-food");
    expect(screen.getByText(/suggérée d'après le nom du commerçant/)).toBeTruthy();
    expect(transactionPosts(calls)).toHaveLength(0);

    // The owner corrects the amount, then adds the transaction.
    fireEvent.change(screen.getByLabelText("Montant"), { target: { value: "6,34" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));
    await waitFor(() => expect(onRecorded).toHaveBeenCalled());
    expect(transactionPosts(calls)[0]?.body).toMatchObject({
      space_id: "s1",
      amount: "6.34",
      tx_date: "2026-10-10",
      merchant: "CARREFOUR MARKET",
      category_id: "c-food",
      source_type: "receipt",
      document_id: "d1",
    });
  });

  it("polls while the receipt is being read", async () => {
    const calls = mockApi({
      statuses: [
        { status: "pending", error: null },
        { status: "processing", error: null },
        { status: "ready", error: null },
      ],
    });
    renderImport();
    expect(await screen.findByText("Lecture du ticket en cours…")).toBeTruthy();
    expect(await screen.findByText("confiance 75 %")).toBeTruthy();
    expect(calls.filter((c) => c.url === "/v1/documents/d1").length).toBeGreaterThanOrEqual(3);
  });

  it("explains a failed reading and offers a manual entry linked to the receipt", async () => {
    const calls = mockApi({ statuses: [{ status: "failed", error: "unreadable" }] });
    renderImport();
    expect(await screen.findByText(/La lecture du ticket a échoué\. Le ticket est illisible/)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/proposal"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Saisir l'opération à la main" }));
    fireEvent.change(screen.getByLabelText("Montant"), { target: { value: "4,90" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));
    await waitFor(() => expect(transactionPosts(calls)).toHaveLength(1));
    expect(transactionPosts(calls)[0]?.body).toMatchObject({
      amount: "4.90",
      source_type: "receipt",
      document_id: "d1",
      tx_date: TODAY,
    });
  });

  it("warns when the receipt was already recorded and asks before a second transaction", async () => {
    mockApi({ answer: { ...proposal, recorded_transaction_ids: ["t0"] } });
    const { onShowTransaction } = renderImport();
    expect(await screen.findByText("Ce ticket a déjà été enregistré")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ajouter l'opération" })).toBeNull();

    const link = await screen.findByRole("link", { name: /Voir l'opération du 10 oct\. 2026/ });
    expect(link.textContent).toContain("−6,43");
    fireEvent.click(link);
    expect(onShowTransaction).toHaveBeenCalledWith(recorded);

    fireEvent.click(screen.getByRole("button", { name: "Ajouter quand même une autre opération" }));
    expect(screen.getByRole("button", { name: "Ajouter l'opération" })).toBeTruthy();
  });

  it("uploads a photo as a receipt of the chosen space, then reads it", async () => {
    const calls = mockApi({ statuses: [{ status: "processing", error: null }, { status: "ready", error: null }] });
    const { onUploaded } = renderImport(null);

    fireEvent.click(screen.getByRole("button", { name: "Lire le ticket" }));
    expect(await screen.findByText("Choisissez l'espace du ticket.")).toBeTruthy();

    const input = screen.getByLabelText("Photo ou PDF du ticket") as HTMLInputElement;
    expect(input.getAttribute("accept")).toBe("image/*,application/pdf");
    expect(input.getAttribute("capture")).toBe("environment");
    fireEvent.change(screen.getByLabelText("Espace"), { target: { value: "s1" } });
    const photo = new File(["fake"], "ticket.jpg", { type: "image/jpeg" });
    fireEvent.change(input, { target: { files: [photo] } });
    fireEvent.click(screen.getByRole("button", { name: "Lire le ticket" }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith("d1"));
    const sent = calls.find((c) => c.url === "/v1/documents" && c.method === "POST");
    expect(sent?.headers["Idempotency-Key"]).toBeTruthy();
    const form = sent?.body as FormData;
    expect(form.get("purpose")).toBe("receipt");
    expect(form.get("space_id")).toBe("s1");
    expect((form.get("file") as File).name).toBe("ticket.jpg");
    expect(await screen.findByText("confiance 75 %")).toBeTruthy();
  });
});
