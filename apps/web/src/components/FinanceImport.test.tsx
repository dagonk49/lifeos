import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { FinanceImport as Import, ImportRow, Space } from "@/lib/types";

import { FinanceImport } from "./FinanceImport";

const SPACES: Space[] = [{ id: "s1", kind: "personal", name: "Perso" }];

const mapping: Import["mapping"] = {
  date_column: 0,
  date_format: "DD/MM/YYYY",
  label_column: 1,
  amount_mode: "signed",
  amount_column: 2,
  debit_column: null,
  credit_column: null,
  decimal_separator: ",",
  reference_column: null,
  currency_column: null,
  currency: "EUR",
};

const preview: Import = {
  id: "i1",
  space_id: "s1",
  status: "preview",
  filename: "releve.csv",
  account_label: null,
  encoding: "cp1252",
  delimiter: ";",
  header: ["Date", "Libellé", "Montant"],
  sample: [["01/09/2026", "CB CARREFOUR MARKET", "-45,30"]],
  suggested_mapping: mapping,
  mapping,
  row_count: 3,
  counts: { valid: 1, possible_duplicate: 1, invalid: 1 },
  to_import: 1,
  totals: { expenses: "45.30", income: "0.00", other_currency: 0 },
  imported_count: 0,
  skipped_count: 0,
  edited_count: 0,
  already_imported_file: false,
  expires_at: "2099-01-01T00:00:00Z",
  committed_at: null,
  undone_at: null,
  version: 2,
  created_at: "2026-10-12T08:00:00Z",
};

function line(line_no: number, status: ImportRow["status"], label: string, amount: string | null, extra: Partial<ImportRow> = {}): ImportRow {
  return {
    line_no,
    status,
    reason: null,
    cells: ["01/09/2026", label, amount ?? ""],
    tx_date: amount ? "2026-09-01" : null,
    amount,
    direction: amount ? "expense" : null,
    currency: amount ? "EUR" : null,
    label,
    merchant: label,
    category_id: null,
    match_id: null,
    match_date: null,
    ...extra,
  };
}

const rows: ImportRow[] = [
  line(2, "valid", "CB CARREFOUR MARKET", "45.30"),
  line(3, "possible_duplicate", "CB CAFE", "2.50", { match_id: "t0", match_date: "2026-08-31" }),
  line(4, "invalid", "CB ERREUR", null, { reason: "amount_precision" }),
];

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function mockApi(): Call[] {
  const calls: Call[] = [];
  let current: Import = preview;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const method = init.method ?? "GET";
      const body = init.body && !(init.body instanceof FormData) ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, headers: init.headers as Record<string, string>, body });
      const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
      if (url === "/v1/finance/imports/i1" && method === "GET") return json(current);
      if (url.startsWith("/v1/finance/imports/i1/rows")) return json({ items: rows, next_cursor: null });
      if (url === "/v1/finance/imports/i1/commit") {
        current = { ...preview, status: "committed", imported_count: 2, skipped_count: 1, counts: {}, totals: null };
        return json(current);
      }
      if (url === "/v1/finance/imports/i1/undo") {
        current = { ...current, status: "undone", edited_count: 0 };
        return json(current);
      }
      return json({ title: "Not found", status: 404, code: "not_found" }, 404);
    }),
  );
  return calls;
}

function renderImport() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const callbacks = { onCreated: vi.fn(), onClose: vi.fn() };
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={SPACES}>
        <FinanceImport importId="i1" {...callbacks} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
  return { invalidate, ...callbacks };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FinanceImport", () => {
  it("writes nothing before the click, leaves possible duplicates out and imports the choice", async () => {
    const calls = mockApi();
    const { invalidate } = renderImport();

    expect(await screen.findByText("Ligne 2")).toBeTruthy();
    expect(screen.getByText("plus de deux décimales (jamais arrondi)")).toBeTruthy();
    expect(screen.getByText(/Ressemble à une opération déjà saisie le 31 août 2026/)).toBeTruthy();
    const list = screen.getByRole("list", { name: "Lignes du relevé" });
    const boxes = within(list).getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, false]);
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);

    // The owner includes the possible duplicate: two operations, exact total.
    fireEvent.click(boxes[1] as HTMLInputElement);
    expect(screen.getByText(/2 opérations : 47,80/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Importer 2 opérations" }));
    await waitFor(() => expect(screen.getByText(/2 nouvelles opérations importées/)).toBeTruthy());
    const commit = calls.find((c) => c.url.endsWith("/commit"));
    expect(commit?.body).toEqual({ exclude_lines: [], include_lines: [3] });
    expect(commit?.headers["Idempotency-Key"]).toBeTruthy();
    const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
    expect(keys).toContain(JSON.stringify(["finance", "transactions"]));
    expect(keys).toContain(JSON.stringify(["finance", "projection"]));
  });

  it("asks for a named confirmation before undoing an import", async () => {
    const calls = mockApi();
    renderImport();
    fireEvent.click(await screen.findByRole("button", { name: /Importer 1 opération/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Annuler cet import" }));
    const group = screen.getByRole("group", { name: "Annuler l'import « releve.csv » ?" });
    expect(calls.some((c) => c.url.endsWith("/undo"))).toBe(false);
    fireEvent.click(within(group).getByRole("button", { name: "Supprimer les opérations de « releve.csv »" }));
    await waitFor(() => expect(screen.getByText(/Import annulé/)).toBeTruthy());
    expect(calls.find((c) => c.url.endsWith("/undo"))?.body).toEqual({ confirm: true });
  });
});
