import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  barWidth,
  budgetPercent,
  budgetState,
  buildCategoryCreate,
  buildCategoryUpdate,
  buildTransactionCreate,
  buildTransactionUpdate,
  categoriesPath,
  categoryChoices,
  categoryFilterOptions,
  centsToDecimal,
  confidenceLabel,
  confidenceTone,
  decimalToInput,
  emptyTransactionForm,
  financeErrorMessage,
  firstError,
  formatCents,
  formatMoney,
  hasBudget,
  idempotencyKeyFor,
  isAutoCategorised,
  isMonth,
  monthLabel,
  ofMonth,
  otherCurrencyNote,
  parseAmount,
  parseBudget,
  parseCategoryName,
  parseMoney,
  proposalPath,
  proposalToForm,
  readingDelay,
  readingErrorMessage,
  readingPollInterval,
  receiptFailure,
  remainingText,
  shiftMonth,
  signedAmount,
  spentText,
  suggestionHint,
  summaryPath,
  summaryRows,
  todayIn,
  toCents,
  transactionSubtitle,
  transactionTitle,
  transactionToForm,
  transactionsPath,
  UNCATEGORISED,
  validateTransaction,
  type TransactionFilters,
  type TransactionFormValues,
} from "./finance";
import type { CategorySummary, FinanceCategory, ReceiptProposal, Transaction } from "./types";

const NBSP = " ";
const NNBSP = " ";
const MINUS = "−";
const TODAY = "2026-10-12";

function category(overrides: Partial<FinanceCategory> = {}): FinanceCategory {
  return {
    id: "c-food",
    space_id: "s1",
    name: "Alimentation",
    kind: "expense",
    monthly_budget: null,
    archived: false,
    created_at: "2026-10-01T08:00:00Z",
    updated_at: "2026-10-01T08:00:00Z",
    ...overrides,
  };
}

function transaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "t1",
    space_id: "s1",
    tx_date: "2026-10-10",
    amount: "12.50",
    direction: "expense",
    currency: "EUR",
    merchant: "Carrefour Market",
    label: null,
    notes: null,
    category_id: null,
    category_confirmed: false,
    source_type: "manual",
    document_id: null,
    version: 2,
    created_at: "2026-10-10T08:00:00Z",
    updated_at: "2026-10-10T08:00:00Z",
    ...overrides,
  };
}

function row(overrides: Partial<CategorySummary> = {}): CategorySummary {
  return {
    category_id: "c-food",
    name: "Alimentation",
    kind: "expense",
    archived: false,
    budget: null,
    spent: "0.00",
    received: "0.00",
    remaining: null,
    ratio: null,
    over_budget: false,
    transactions: 0,
    ...overrides,
  };
}

function form(overrides: Partial<TransactionFormValues> = {}): TransactionFormValues {
  return { ...emptyTransactionForm(TODAY), amount: "12,50", merchant: "Boulangerie", ...overrides };
}

describe("exact money", () => {
  it("turns API decimals into integer cents without floating point", () => {
    expect(toCents("12.50")).toBe(1250n);
    expect(toCents("12.3")).toBe(1230n);
    expect(toCents("2000")).toBe(200000n);
    expect(toCents("-5.00")).toBe(-500n);
    expect(toCents("0.10")).toBe(10n);
    expect(toCents(".5")).toBe(50n);
    expect(toCents("99999999999999.99")).toBe(9999999999999999n);
    // Never sent by the API, rounded half away from zero just in case.
    expect(toCents("1.005")).toBe(101n);
    expect(toCents("-1.005")).toBe(-101n);
    expect(toCents("abc")).toBeNull();
    expect(toCents("")).toBeNull();
    expect(toCents(null)).toBeNull();
    expect(centsToDecimal(123450n)).toBe("1234.50");
    expect(centsToDecimal(-5n)).toBe("-0.05");
    expect(centsToDecimal(0n)).toBe("0.00");
  });

  it("formats amounts in French, exactly", () => {
    expect(formatMoney("1234.5")).toBe(`1${NNBSP}234,50${NBSP}€`);
    expect(formatMoney("0.30")).toBe(`0,30${NBSP}€`);
    expect(formatMoney("1234567.89")).toBe(`1${NNBSP}234${NNBSP}567,89${NBSP}€`);
    expect(formatMoney("-5.00")).toBe(`${MINUS}5,00${NBSP}€`);
    expect(formatMoney("100")).toBe(`100,00${NBSP}€`);
    // Floats would print 0.30000000000000004 or lose cents on large values.
    expect(formatMoney("90071992547409.93")).toBe(`90${NNBSP}071${NNBSP}992${NNBSP}547${NNBSP}409,93${NBSP}€`);
    expect(formatMoney("12.00", "USD")).toMatch(/^12,00 \S*\$/);
    expect(formatMoney(null)).toBe("");
    expect(formatMoney("n/a")).toBe("n/a");
    expect(formatCents(150n, "EUR", "always")).toBe(`+1,50${NBSP}€`);
    expect(formatCents(0n, "EUR", "always")).toBe(`0,00${NBSP}€`);
  });

  it("signs transaction amounts by direction", () => {
    expect(signedAmount({ amount: "12.50", direction: "expense", currency: "EUR" })).toBe(`${MINUS}12,50${NBSP}€`);
    expect(signedAmount({ amount: "1200", direction: "income", currency: "EUR" })).toBe(`+1${NNBSP}200,00${NBSP}€`);
  });

  it("parses French amounts into canonical decimals", () => {
    const ok = (raw: string) => parseMoney(raw);
    expect(ok("12,5")).toEqual({ ok: true, value: "12.50" });
    expect(ok("12.50")).toEqual({ ok: true, value: "12.50" });
    expect(ok("1 234,56")).toEqual({ ok: true, value: "1234.56" });
    expect(ok(`1${NNBSP}234,56`)).toEqual({ ok: true, value: "1234.56" });
    expect(ok(`1${NBSP}234,56 €`)).toEqual({ ok: true, value: "1234.56" });
    expect(ok("1.234,56")).toEqual({ ok: true, value: "1234.56" });
    expect(ok("1,234.56")).toEqual({ ok: true, value: "1234.56" });
    expect(ok("€ 3")).toEqual({ ok: true, value: "3.00" });
    expect(ok("12 EUR")).toEqual({ ok: true, value: "12.00" });
    expect(ok(",5")).toEqual({ ok: true, value: "0.50" });
    expect(ok("12,")).toEqual({ ok: true, value: "12.00" });
    expect(ok("007,10")).toEqual({ ok: true, value: "7.10" });
    expect(ok("+4")).toEqual({ ok: true, value: "4.00" });
    expect(ok("  ")).toEqual({ ok: true, value: null });
    expect(ok("999999999999,99")).toEqual({ ok: true, value: "999999999999.99" });
  });

  it("rejects invalid amounts with French messages", () => {
    const error = (raw: string, allowZero = false) => {
      const result = parseMoney(raw, { allowZero });
      return result.ok ? null : result.error;
    };
    expect(error("12,345")).toMatch(/Deux décimales au plus/);
    expect(error("1.234")).toMatch(/Deux décimales au plus/);
    expect(error("-5")).toBe("Le montant ne peut pas être négatif.");
    expect(error(`${MINUS}5`)).toBe("Le montant ne peut pas être négatif.");
    expect(error("0")).toBe("Le montant doit être supérieur à zéro.");
    expect(error("0,00", true)).toBeNull();
    expect(error("abc")).toMatch(/^Montant invalide/);
    expect(error("1.234.56")).toMatch(/^Montant invalide/);
    expect(error("12,5,0")).toMatch(/^Montant invalide/);
    expect(error("1 23,4")).toMatch(/^Montant invalide/);
    expect(error(",")).toMatch(/^Montant invalide/);
    expect(error("1e3")).toMatch(/^Montant invalide/);
    expect(error("1000000000000")).toMatch(/trop élevé/);
  });

  it("requires a positive amount for transactions, allows zero or nothing for budgets", () => {
    expect(parseAmount("")).toEqual({ ok: false, error: "Indiquez un montant." });
    expect(parseAmount("6,43")).toEqual({ ok: true, value: "6.43" });
    expect(parseBudget("")).toEqual({ ok: true, value: null });
    expect(parseBudget("0")).toEqual({ ok: true, value: "0.00" });
    expect(parseBudget("300")).toEqual({ ok: true, value: "300.00" });
  });

  it("turns API decimals into editable French values", () => {
    expect(decimalToInput("12.5")).toBe("12,50");
    expect(decimalToInput("1234.00")).toBe("1234,00");
    expect(decimalToInput(null)).toBe("");
  });
});

describe("months", () => {
  it("computes today in the owner's time zone", () => {
    // 22:30 UTC on 30 September is already 1 October in Paris, not yet in Honolulu.
    const instant = new Date("2026-09-30T22:30:00Z");
    expect(todayIn("Europe/Paris", instant)).toBe("2026-10-01");
    expect(todayIn("Pacific/Honolulu", instant)).toBe("2026-09-30");
    expect(todayIn("UTC", instant)).toBe("2026-09-30");
    // An unknown zone falls back to the browser's day (whatever it is, a valid date).
    expect(todayIn("Not/AZone", instant)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("navigates and labels months", () => {
    expect(shiftMonth("2026-10", -1)).toBe("2026-09");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-10", 14)).toBe("2027-12");
    expect(monthLabel("2026-10")).toBe("octobre 2026");
    expect(monthLabel("2027-02")).toBe("février 2027");
    expect(ofMonth("2026-10")).toBe("d'octobre 2026");
    expect(ofMonth("2026-08")).toBe("d'août 2026");
    expect(ofMonth("2026-04")).toBe("d'avril 2026");
    expect(ofMonth("2026-09")).toBe("de septembre 2026");
    expect(isMonth("2026-10")).toBe(true);
    expect(isMonth("2026-13")).toBe(false);
    expect(isMonth("2026-9")).toBe(false);
    expect(isMonth(null)).toBe(false);
  });
});

describe("API paths", () => {
  const filters: TransactionFilters = { spaceId: null, month: "2026-10", direction: "", category: "", q: "" };

  it("builds the transactions query", () => {
    expect(transactionsPath(filters)).toBe("/v1/finance/transactions?month=2026-10&limit=50");
    expect(
      transactionsPath(
        { spaceId: "s1", month: "2026-09", direction: "expense", category: "c1", q: "  pain & lait " },
        "abc",
        20,
      ),
    ).toBe(
      "/v1/finance/transactions?space_id=s1&month=2026-09&direction=expense&category_id=c1&q=pain+%26+lait&limit=20&cursor=abc",
    );
    expect(transactionsPath({ ...filters, category: UNCATEGORISED })).toContain("uncategorised=true");
    expect(transactionsPath({ ...filters, category: UNCATEGORISED })).not.toContain("category_id");
  });

  it("builds the other paths", () => {
    expect(categoriesPath(null, false)).toBe("/v1/finance/categories");
    expect(categoriesPath("s1", true)).toBe("/v1/finance/categories?space_id=s1&include_archived=true");
    expect(summaryPath("s1", "2026-10")).toBe("/v1/finance/summary?space_id=s1&month=2026-10");
    expect(proposalPath("d/1")).toBe("/v1/finance/receipts/d%2F1/proposal");
  });
});

describe("errors", () => {
  it("explains the finance codes and falls back to the shared messages", () => {
    const problem = (code: string) => new ApiError(409, { title: "x", status: 409, code });
    expect(financeErrorMessage(problem("category_in_use"))).toMatch(/ne peut plus passer de dépense à revenu/);
    expect(financeErrorMessage(problem("category_exists"))).toBe("Une catégorie porte déjà ce nom dans cet espace.");
    expect(financeErrorMessage(new Error("boom"))).toBe("boom");
  });
});

describe("transactions", () => {
  it("titles a transaction from its merchant or label", () => {
    expect(transactionTitle(transaction())).toBe("Carrefour Market");
    expect(transactionTitle(transaction({ merchant: null, label: "Loyer" }))).toBe("Loyer");
    expect(transactionTitle(transaction({ merchant: null, source_type: "receipt" }))).toBe("Ticket de caisse");
    expect(transactionTitle(transaction({ merchant: null }))).toBe("Opération sans libellé");
    expect(transactionSubtitle(transaction({ label: "Courses" }))).toBe("Courses");
    expect(transactionSubtitle(transaction({ merchant: null, label: "Courses" }))).toBeNull();
    expect(isAutoCategorised(transaction({ category_id: "c1" }))).toBe(true);
    expect(isAutoCategorised(transaction({ category_id: "c1", category_confirmed: true }))).toBe(false);
    expect(isAutoCategorised(transaction())).toBe(false);
  });

  it("offers the active categories of the space and kind, plus the current archived one", () => {
    const categories = [
      category({ id: "c-rest", name: "Restaurants" }),
      category({ id: "c-food", name: "Alimentation" }),
      category({ id: "c-salary", name: "Salaire", kind: "income" }),
      category({ id: "c-old", name: "Vacances", archived: true }),
      category({ id: "c-pro", name: "Fournitures", space_id: "s2" }),
    ];
    expect(categoryChoices(categories, "s1", "expense").map((c) => c.id)).toEqual(["c-food", "c-rest"]);
    expect(categoryChoices(categories, "s1", "income").map((c) => c.id)).toEqual(["c-salary"]);
    expect(categoryChoices(categories, "s1", "expense", "c-old").map((c) => c.id)).toEqual([
      "c-food",
      "c-rest",
      "c-old",
    ]);
    expect(categoryChoices(categories, "s1", "expense", "c-food").map((c) => c.id)).toEqual(["c-food", "c-rest"]);

    const options = categoryFilterOptions(categories, (id) => (id === "s1" ? "Perso" : "Pro"));
    expect(options.slice(0, 2)).toEqual([
      { value: "", label: "Toutes les catégories" },
      { value: UNCATEGORISED, label: "Non classées" },
    ]);
    expect(options.map((o) => o.label)).toContain("Vacances · Perso (archivée)");
    expect(options.map((o) => o.label)).toContain("Fournitures · Pro");
    // Expenses first, then incomes.
    expect(options.at(-1)?.label).toBe("Salaire · Perso");
    expect(categoryFilterOptions([category()]).at(-1)?.label).toBe("Alimentation");
  });

  it("validates the form with French messages", () => {
    expect(validateTransaction(form(), TODAY)).toEqual({
      ok: true,
      value: {
        amount: "12.50",
        direction: "expense",
        currency: "EUR",
        tx_date: TODAY,
        merchant: "Boulangerie",
        label: null,
        notes: null,
        category_id: null,
      },
    });
    const errors = (values: Partial<TransactionFormValues>) => {
      const result = validateTransaction(form(values), TODAY);
      return result.ok ? {} : result.errors;
    };
    expect(errors({ amount: "" })).toEqual({ amount: "Indiquez un montant." });
    expect(errors({ amount: "12,345" }).amount).toMatch(/Deux décimales/);
    expect(errors({ tx_date: "2026-10-13" })).toEqual({ tx_date: "La date ne peut pas être dans le futur." });
    expect(errors({ tx_date: "" })).toEqual({ tx_date: "Indiquez la date de l'opération." });
    expect(errors({ tx_date: "2026-02-30" })).toEqual({ tx_date: "Date invalide." });
    expect(errors({ currency: "eur" }).currency).toMatch(/Devise invalide/);
    expect(errors({ merchant: "x".repeat(121) }).merchant).toMatch(/120 caractères/);
    expect(errors({ label: "x".repeat(201) }).label).toMatch(/200 caractères/);
    expect(errors({ notes: "x".repeat(2001) }).notes).toMatch(/2000 caractères/);
    const several = errors({ amount: "0", tx_date: "2030-01-01" });
    expect(Object.keys(several)).toEqual(["amount", "tx_date"]);
    expect(firstError(several)).toBe("amount");
    expect(firstError({})).toBeNull();
  });

  it("cleans the text fields like the server", () => {
    const result = validateTransaction(
      form({ merchant: "  Boulangerie   du Marché ", label: "  ", notes: "  Pour le\ndimanche " }),
      TODAY,
    );
    expect(result.ok && result.value).toMatchObject({
      merchant: "Boulangerie du Marché",
      label: null,
      notes: "Pour le\ndimanche",
    });
  });

  it("builds a manual or a receipt creation, and needs a space", () => {
    const manual = buildTransactionCreate(form({ category_id: "c-food" }), { spaceId: "s1", today: TODAY });
    expect(manual).toEqual({
      ok: true,
      value: {
        space_id: "s1",
        amount: "12.50",
        direction: "expense",
        currency: "EUR",
        tx_date: TODAY,
        merchant: "Boulangerie",
        label: null,
        notes: null,
        category_id: "c-food",
        source_type: "manual",
      },
    });
    const receipt = buildTransactionCreate(form(), { spaceId: "s1", today: TODAY, receiptDocumentId: "d1" });
    expect(receipt.ok && receipt.value).toMatchObject({ source_type: "receipt", document_id: "d1" });
    expect(buildTransactionCreate(form(), { spaceId: null, today: TODAY })).toEqual({
      ok: false,
      errors: { space: "Choisissez un espace." },
    });
    const both = buildTransactionCreate(form({ amount: "" }), { spaceId: "", today: TODAY });
    expect(both.ok ? null : both.errors).toEqual({ space: "Choisissez un espace.", amount: "Indiquez un montant." });
  });

  it("round-trips a transaction through the form and patches only what changed", () => {
    const tx = transaction({ category_id: "c-food", label: "Courses", amount: "12.5" });
    const values = transactionToForm(tx);
    expect(values).toMatchObject({ amount: "12,50", category_id: "c-food", label: "Courses", notes: "" });
    // "12,50" equals "12.5": not a change; the automatic category is not confirmed by saving.
    expect(buildTransactionUpdate(tx, values, TODAY)).toEqual({ ok: true, value: {} });
    expect(buildTransactionUpdate(tx, { ...values, amount: "13", notes: "Pain" }, TODAY)).toEqual({
      ok: true,
      value: { amount: "13.00", notes: "Pain" },
    });
    expect(buildTransactionUpdate(tx, { ...values, category_id: "" }, TODAY)).toEqual({
      ok: true,
      value: { category_id: null },
    });
    expect(buildTransactionUpdate(tx, { ...values, direction: "income", merchant: "" }, TODAY)).toEqual({
      ok: true,
      value: { direction: "income", merchant: null },
    });
    const invalid = buildTransactionUpdate(tx, { ...values, tx_date: "2027-01-01" }, TODAY);
    expect(invalid.ok).toBe(false);
  });

  it("keeps the idempotency key while the body is unchanged", () => {
    let n = 0;
    const make = () => `key-${++n}`;
    const first = idempotencyKeyFor(null, { amount: "1.00" }, make);
    expect(first.key).toBe("key-1");
    expect(idempotencyKeyFor(first, { amount: "1.00" }, make)).toBe(first);
    expect(idempotencyKeyFor(first, { amount: "2.00" }, make).key).toBe("key-2");
  });
});

describe("categories", () => {
  it("validates names like the server", () => {
    expect(parseCategoryName("  Vacances   d'été ")).toEqual({ ok: true, value: "Vacances d'été" });
    expect(parseCategoryName("  ")).toEqual({ ok: false, error: "Donnez un nom à la catégorie." });
    expect(parseCategoryName("x".repeat(61))).toEqual({
      ok: false,
      error: "Le nom ne doit pas dépasser 60 caractères.",
    });
  });

  it("builds a creation with an optional budget (expenses only)", () => {
    expect(buildCategoryCreate({ name: " Vacances ", kind: "expense", budget: "300" }, "s1")).toEqual({
      ok: true,
      value: { space_id: "s1", name: "Vacances", kind: "expense", monthly_budget: "300.00" },
    });
    expect(buildCategoryCreate({ name: "Primes", kind: "income", budget: "300" }, "s1")).toEqual({
      ok: true,
      value: { space_id: "s1", name: "Primes", kind: "income", monthly_budget: null },
    });
    const invalid = buildCategoryCreate({ name: "Vacances", kind: "expense", budget: "1,001" }, "s1");
    expect(invalid.ok ? null : invalid.error).toMatch(/^Budget : Deux décimales/);
    expect(buildCategoryCreate({ name: "", kind: "expense", budget: "" }, "s1").ok).toBe(false);
  });

  it("patches only the changed name or budget; an empty budget removes it", () => {
    const current = category({ monthly_budget: "300.00" });
    expect(buildCategoryUpdate(current, { name: "Alimentation", budget: "300" })).toEqual({ ok: true, value: {} });
    expect(buildCategoryUpdate(current, { name: "Courses", budget: "300,00" })).toEqual({
      ok: true,
      value: { name: "Courses" },
    });
    expect(buildCategoryUpdate(current, { name: "Alimentation", budget: "" })).toEqual({
      ok: true,
      value: { monthly_budget: null },
    });
    expect(buildCategoryUpdate(category(), { name: "Alimentation", budget: "250,5" })).toEqual({
      ok: true,
      value: { monthly_budget: "250.50" },
    });
    expect(buildCategoryUpdate(current, { name: "Alimentation", budget: "-1" }).ok).toBe(false);
  });
});

describe("budget summary", () => {
  it("describes the budget state with text, not only colour", () => {
    expect(budgetState(row())).toBe("none");
    expect(budgetState(row({ budget: "100.00", ratio: 0.5 }))).toBe("ok");
    expect(budgetState(row({ budget: "0.00", ratio: null }))).toBe("ok");
    expect(budgetState(row({ budget: "100.00", ratio: 0.9 }))).toBe("near");
    expect(budgetState(row({ budget: "20.00", ratio: 1.25, over_budget: true }))).toBe("over");
    expect(budgetPercent(0.003)).toBe(0);
    expect(budgetPercent(1.25)).toBe(125);
    expect(budgetPercent(null)).toBeNull();
    expect(barWidth(1.25)).toBe(100);
    expect(barWidth(0.9)).toBe(90);
    expect(barWidth(null)).toBe(0);
  });

  it("writes spent, budget and remaining amounts exactly", () => {
    expect(spentText(row({ spent: "45.00", budget: "50.00", ratio: 0.9 }))).toBe(
      `45,00${NBSP}€ sur 50,00${NBSP}€ (90${NBSP}%)`,
    );
    expect(spentText(row({ spent: "12.00" }))).toBe(`12,00${NBSP}€ dépensés`);
    expect(remainingText("99.70")).toBe(`Reste 99,70${NBSP}€`);
    expect(remainingText("-5.00")).toBe(`Dépassement de 5,00${NBSP}€`);
    expect(remainingText(null)).toBeNull();
  });

  it("keeps the meaningful rows and notes the other currencies", () => {
    const summary = {
      categories: [
        row({ category_id: "a", budget: "100.00" }),
        row({ category_id: "b", transactions: 2, spent: "3.00" }),
        row({ category_id: "c" }),
        row({ category_id: "d", kind: "income", transactions: 1, received: "2000.00" }),
        row({ category_id: "e", kind: "income" }),
      ],
    };
    const rows = summaryRows(summary);
    expect(rows.expenses.map((r) => r.category_id)).toEqual(["a", "b"]);
    expect(rows.incomes.map((r) => r.category_id)).toEqual(["d"]);
    const totals = { expenses: "0", income: "0", balance: "0", budget_remaining: "0", budgeted_spent: "0" };
    expect(hasBudget({ totals: { ...totals, budget: "0.00" } })).toBe(false);
    expect(hasBudget({ totals: { ...totals, budget: "170.00" } })).toBe(true);
    expect(otherCurrencyNote(0)).toBeNull();
    expect(otherCurrencyNote(1)).toBe("1 opération dans une autre devise n'est pas comptée.");
    expect(otherCurrencyNote(3)).toBe("3 opérations dans une autre devise ne sont pas comptées.");
  });
});

describe("receipts", () => {
  const proposal: ReceiptProposal = {
    document_id: "d1",
    space_id: "s1",
    title: "ticket.jpg",
    merchant: "CARREFOUR MARKET",
    tx_date: "2026-10-10",
    total: "6.43",
    currency: "EUR",
    vat_rates: ["5,5"],
    card_payment: true,
    confidence: 0.75,
    warnings: [],
    lines: { merchant: "CARREFOUR MARKET", tx_date: "10/10/2026 18:42", total: "TOTAL TTC 6,43 EUR" },
    suggested_category: { id: "c-food", name: "Alimentation", source: "keyword" },
    recorded_transaction_ids: [],
  };

  it("prefills the form with what was read", () => {
    expect(proposalToForm(proposal, TODAY)).toEqual({
      amount: "6,43",
      direction: "expense",
      currency: "EUR",
      tx_date: "2026-10-10",
      merchant: "CARREFOUR MARKET",
      label: "",
      category_id: "c-food",
      notes: "",
    });
    const unread = proposalToForm(
      { ...proposal, merchant: null, total: null, tx_date: null, suggested_category: null, currency: "USD" },
      TODAY,
    );
    expect(unread).toMatchObject({ amount: "", merchant: "", tx_date: TODAY, category_id: "", currency: "USD" });
  });

  it("labels the confidence and the suggestion", () => {
    expect(confidenceLabel(0.75)).toBe(`confiance 75${NBSP}%`);
    expect(confidenceLabel(1.4)).toBe(`confiance 100${NBSP}%`);
    expect(confidenceTone(0.9)).toBe("teal");
    expect(confidenceTone(0.75)).toBe("amber");
    expect(confidenceTone(0.25)).toBe("red");
    expect(suggestionHint(null)).toBeNull();
    expect(suggestionHint({ id: "c", name: "Divers", source: "rule" })).toMatch(/vos choix précédents/);
    expect(suggestionHint({ id: "c", name: "Divers", source: "keyword" })).toMatch(/nom du commerçant/);
  });

  it("polls while the receipt is read and explains failures", () => {
    expect(readingPollInterval({ status: "pending", error: null })).toBe(2000);
    expect(readingPollInterval({ status: "processing", error: null }, 10)).toBe(10);
    expect(readingPollInterval({ status: "pending", error: "extraction_unavailable" })).toBe(10000);
    expect(readingPollInterval({ status: "ready", error: null })).toBe(false);
    expect(readingPollInterval(undefined)).toBe(false);

    expect(receiptFailure({ status: "ready", error: null, purpose: "receipt" })).toBeNull();
    expect(receiptFailure({ status: "failed", error: "unreadable", purpose: "receipt" })).toMatch(/illisible/);
    expect(receiptFailure({ status: "rejected", error: null, purpose: "receipt" })).toMatch(/refusé/);
    expect(receiptFailure({ status: "ready", error: null, purpose: "document" })).toBe(
      "Ce document n'est pas un ticket de caisse.",
    );
    expect(readingErrorMessage("weird")).toBe("La lecture a échoué (code « weird »).");
    expect(readingErrorMessage(null)).toBe("La lecture a échoué.");
    expect(readingDelay({ status: "pending", error: "extraction_unavailable" })).toMatch(/nouvel essai automatique/);
    expect(readingDelay({ status: "processing", error: null })).toBeNull();
  });
});
