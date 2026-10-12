import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { FinanceCategory, Space, Transaction } from "@/lib/types";

import { TransactionForm } from "./TransactionForm";

const TODAY = "2026-10-12";
const SPACES: Space[] = [
  { id: "s1", kind: "personal", name: "Perso" },
  { id: "s2", kind: "professional", name: "Pro" },
];

function category(id: string, name: string, kind: "expense" | "income" = "expense", space_id = "s1"): FinanceCategory {
  return {
    id,
    space_id,
    name,
    kind,
    monthly_budget: null,
    archived: false,
    created_at: "2026-10-01T08:00:00Z",
    updated_at: "2026-10-01T08:00:00Z",
  };
}

const CATEGORIES = [
  category("c-food", "Alimentation"),
  category("c-rest", "Restaurants"),
  category("c-salary", "Salaire", "income"),
];

const saved: Transaction = {
  id: "t1",
  space_id: "s1",
  tx_date: TODAY,
  amount: "1234.50",
  direction: "expense",
  currency: "EUR",
  merchant: "Boulangerie",
  label: null,
  notes: null,
  category_id: "c-food",
  category_confirmed: true,
  source_type: "manual",
  document_id: null,
  version: 1,
  created_at: "2026-10-12T08:00:00Z",
  updated_at: "2026-10-12T08:00:00Z",
};

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function mockApi({ failFirstPost = false } = {}): Call[] {
  const calls: Call[] = [];
  let posts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const method = init.method ?? "GET";
      calls.push({
        url,
        method,
        headers: init.headers as Record<string, string>,
        body: init.body ? JSON.parse(init.body as string) : undefined,
      });
      if (url.startsWith("/v1/finance/categories")) {
        const space = new URL(url, "http://x").searchParams.get("space_id");
        return new Response(JSON.stringify(space === "s2" ? [category("c-pro", "Fournitures", "expense", "s2")] : CATEGORIES));
      }
      if (method === "POST") {
        posts += 1;
        if (failFirstPost && posts === 1) throw new TypeError("Failed to fetch");
        return new Response(JSON.stringify(saved), { status: 201 });
      }
      if (method === "PATCH") return new Response(JSON.stringify({ ...saved, version: 4 }));
      return new Response("null", { status: 404 });
    }),
  );
  return calls;
}

function renderForm(props: Partial<Parameters<typeof TransactionForm>[0]> = {}) {
  const onSaved = vi.fn();
  const onCancel = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={SPACES}>
        <TransactionForm today={TODAY} onSaved={onSaved} onCancel={onCancel} {...props} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
  return { onSaved, onCancel };
}

const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TransactionForm", () => {
  it("creates an expense typed in French, with an idempotency key", async () => {
    const calls = mockApi();
    const { onSaved } = renderForm({ spaceId: "s1" });
    await screen.findByRole("option", { name: "Alimentation" });
    // Expense categories only.
    expect(screen.queryByRole("option", { name: "Salaire" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Montant"), { target: { value: "1 234,5" } });
    fireEvent.change(screen.getByLabelText("Commerçant"), { target: { value: "  Boulangerie " } });
    fireEvent.change(screen.getByLabelText("Catégorie"), { target: { value: "c-food" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    const [post] = posts(calls);
    expect(post?.url).toBe("/v1/finance/transactions");
    expect(post?.headers["Idempotency-Key"]).toBeTruthy();
    expect(post?.body).toEqual({
      space_id: "s1",
      amount: "1234.50",
      direction: "expense",
      currency: "EUR",
      tx_date: TODAY,
      merchant: "Boulangerie",
      label: null,
      notes: null,
      category_id: "c-food",
      source_type: "manual",
    });
  });

  it("explains invalid values in French and sends nothing", async () => {
    const calls = mockApi();
    renderForm({ spaceId: "s1" });
    const amount = screen.getByLabelText("Montant");
    fireEvent.change(amount, { target: { value: "12,345" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-10-13" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));

    expect(await screen.findByText("Deux décimales au plus (les centimes).")).toBeTruthy();
    expect(screen.getByText("La date ne peut pas être dans le futur.")).toBeTruthy();
    expect(amount.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(amount);
    expect(posts(calls)).toHaveLength(0);

    // Correcting the field clears its message.
    fireEvent.change(amount, { target: { value: "12,34" } });
    expect(screen.queryByText("Deux décimales au plus (les centimes).")).toBeNull();
  });

  it("retries with the same idempotency key after a network failure", async () => {
    const calls = mockApi({ failFirstPost: true });
    const { onSaved } = renderForm({ spaceId: "s1" });
    fireEvent.change(screen.getByLabelText("Montant"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));
    expect(await screen.findByText("Failed to fetch")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const [first, second] = posts(calls);
    expect(first?.headers["Idempotency-Key"]).toBe(second?.headers["Idempotency-Key"]);
  });

  it("asks for a space when none is selected, then offers its categories", async () => {
    const calls = mockApi();
    renderForm();
    fireEvent.change(screen.getByLabelText("Montant"), { target: { value: "3,20" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));
    expect(await screen.findByText("Choisissez un espace.")).toBeTruthy();
    expect(posts(calls)).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Espace"), { target: { value: "s2" } });
    expect(await screen.findByRole("option", { name: "Fournitures" })).toBeTruthy();
    expect(screen.queryByText("Choisissez un espace.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ajouter l'opération" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]?.body).toMatchObject({ space_id: "s2", amount: "3.20" });
  });

  it("drops a category that no longer fits when the direction changes", async () => {
    mockApi();
    renderForm({ spaceId: "s1" });
    await screen.findByRole("option", { name: "Alimentation" });
    const select = screen.getByLabelText("Catégorie") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "c-food" } });
    fireEvent.click(screen.getByLabelText("Revenu"));
    expect(select.value).toBe("");
    expect(screen.getByRole("option", { name: "Salaire" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Alimentation" })).toBeNull();
  });

  it("patches only the changed fields with the transaction version", async () => {
    const calls = mockApi();
    const transaction: Transaction = { ...saved, amount: "12.50", version: 3, category_confirmed: false };
    const { onSaved } = renderForm({ transaction });
    expect((screen.getByLabelText("Montant") as HTMLInputElement).value).toBe("12,50");
    await screen.findByRole("option", { name: "Alimentation" });

    fireEvent.change(screen.getByLabelText("Montant"), { target: { value: "13" } });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer les modifications" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/v1/finance/transactions/t1");
    expect(patch?.headers["If-Match"]).toBe("3");
    // The automatic category is not confirmed (nor taught) behind the owner's back.
    expect(patch?.body).toEqual({ amount: "13.00" });
  });
});
