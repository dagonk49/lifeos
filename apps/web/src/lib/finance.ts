// Pure helpers for the finances page (F04, UC02): exact money, months, forms, labels.
//
// Money never goes through floating point: API amounts are decimal strings, turned into
// integer cents (BigInt) for formatting and comparison.

import { ApiError, errorMessage } from "./api";
import type {
  BudgetSummary,
  CategorySummary,
  Decimal,
  DocumentSummary,
  FinanceCategory,
  ReceiptProposal,
  Transaction,
} from "./types";

export type Direction = Transaction["direction"];
export type CategoryKind = FinanceCategory["kind"];
export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };
type Tone = "neutral" | "teal" | "amber" | "red" | "blue" | "violet";

const NBSP = " ";
/** Thousands separator, as written by Intl in French. */
const NNBSP = " ";
const MINUS = "−";

// --- Money -----------------------------------------------------------------------------

const API_DECIMAL = /^([+-])?(\d*)(?:\.(\d*))?$/;

/** API decimal string ("-5.00", "12.3", "2000") -> integer cents; null if not a decimal. */
export function toCents(value: Decimal | null | undefined): bigint | null {
  if (value === null || value === undefined) return null;
  const match = API_DECIMAL.exec(value.trim());
  if (!match) return null;
  const [, sign, whole = "", fraction = ""] = match;
  if (!whole && !fraction) return null;
  let cents = BigInt(`${whole || "0"}${fraction.slice(0, 2).padEnd(2, "0")}`);
  // The API never sends more than two decimals; round half away from zero if it did.
  if ((fraction[2] ?? "0") >= "5") cents += 1n;
  return sign === "-" ? -cents : cents;
}

/** Integer cents -> canonical API decimal ("1234.50"). */
export function centsToDecimal(cents: bigint): Decimal {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const text = `${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
  return negative ? `-${text}` : text;
}

const symbols = new Map<string, string>();

/** "EUR" -> "€" (Intl, French); an unknown code is shown as is. */
export function currencySymbol(currency: string): string {
  let symbol = symbols.get(currency);
  if (symbol === undefined) {
    try {
      const parts = new Intl.NumberFormat("fr-FR", { style: "currency", currency }).formatToParts(0);
      symbol = parts.find((part) => part.type === "currency")?.value ?? currency;
    } catch {
      symbol = currency;
    }
    symbols.set(currency, symbol);
  }
  return symbol;
}

/** "negative": a sign only below zero; "always": "+" above zero too (signed amounts). */
export type SignDisplay = "negative" | "always";

export function formatCents(cents: bigint, currency = "EUR", sign: SignDisplay = "negative"): string {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const whole = String(abs / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, NNBSP);
  const text = `${whole},${String(abs % 100n).padStart(2, "0")}${NBSP}${currencySymbol(currency)}`;
  if (negative) return `${MINUS}${text}`;
  return sign === "always" && abs > 0n ? `+${text}` : text;
}

/** Exact French display: "1234.5" -> "1 234,50 €" (no floating-point rounding). */
export function formatMoney(
  value: Decimal | null | undefined,
  currency = "EUR",
  sign: SignDisplay = "negative",
): string {
  const cents = toCents(value);
  if (cents === null) return value ?? "";
  return formatCents(cents, currency, sign);
}

/** Signed amount of a transaction: expense "−12,50 €", income "+1 200,00 €". */
export function signedAmount(tx: Pick<Transaction, "amount" | "direction" | "currency">): string {
  const cents = toCents(tx.amount) ?? 0n;
  return formatCents(tx.direction === "expense" ? -cents : cents, tx.currency, "always");
}

/** API decimal -> editable French value ("12.5" -> "12,50"). */
export function decimalToInput(value: Decimal | null | undefined): string {
  const cents = toCents(value);
  return cents === null ? "" : centsToDecimal(cents).replace(".", ",");
}

export const MONEY_ERRORS = {
  required: "Indiquez un montant.",
  invalid: "Montant invalide : écrivez par exemple 12,50 ou 1 234,56.",
  precision: "Deux décimales au plus (les centimes).",
  negative: "Le montant ne peut pas être négatif.",
  zero: "Le montant doit être supérieur à zéro.",
  tooLarge: "Montant trop élevé (12 chiffres avant la virgule au plus).",
} as const;

// numeric(14, 2) on the server: 12 digits before the decimal separator.
const MAX_WHOLE_DIGITS = 12;
const GROUPED = /^\d{1,3}(?:[ .,]\d{3})+$/;

/**
 * French user input -> canonical API decimal with two decimals ("12,5" -> "12.50",
 * "1 234,56" -> "1234.56"); "" -> null. The last comma or dot separates the cents;
 * spaces (or the other separator) group thousands. Never goes through a float.
 */
export function parseMoney(raw: string, options: { allowZero?: boolean } = {}): Parsed<Decimal | null> {
  let text = raw
    .replace(/[\s  ]+/g, " ")
    .trim()
    .replace(/^€\s*/, "")
    .replace(/\s*(€|eur)$/i, "")
    .trim();
  if (text === "") return { ok: true, value: null };
  if (text.startsWith("-") || text.startsWith(MINUS)) return { ok: false, error: MONEY_ERRORS.negative };
  if (text.startsWith("+")) text = text.slice(1).trim();

  const at = Math.max(text.lastIndexOf(","), text.lastIndexOf("."));
  let whole = text;
  let fraction = "";
  if (at >= 0) {
    const separator = text.charAt(at);
    whole = text.slice(0, at).trim();
    fraction = text.slice(at + 1).trim();
    // "1.234.56" or "1,2,3": the decimal separator appears twice.
    if (whole.includes(separator)) return { ok: false, error: MONEY_ERRORS.invalid };
    if (!/^\d*$/.test(fraction)) return { ok: false, error: MONEY_ERRORS.invalid };
    if (fraction.length > 2) return { ok: false, error: MONEY_ERRORS.precision };
  }
  if (whole === "" && fraction === "") return { ok: false, error: MONEY_ERRORS.invalid };
  if (whole !== "" && !/^\d+$/.test(whole) && !GROUPED.test(whole)) {
    return { ok: false, error: MONEY_ERRORS.invalid };
  }
  const digits = whole.replace(/[ .,]/g, "").replace(/^0+(?=\d)/, "") || "0";
  if (digits.length > MAX_WHOLE_DIGITS) return { ok: false, error: MONEY_ERRORS.tooLarge };
  const value = `${digits}.${fraction.padEnd(2, "0")}`;
  if (!options.allowZero && value === "0.00") return { ok: false, error: MONEY_ERRORS.zero };
  return { ok: true, value };
}

/** A transaction amount: required and strictly positive. */
export function parseAmount(raw: string): Parsed<Decimal> {
  const parsed = parseMoney(raw);
  if (!parsed.ok) return parsed;
  if (parsed.value === null) return { ok: false, error: MONEY_ERRORS.required };
  return { ok: true, value: parsed.value };
}

/** A monthly budget: zero or more; "" removes it (null). */
export function parseBudget(raw: string): Parsed<Decimal | null> {
  return parseMoney(raw, { allowZero: true });
}

function sameMoney(a: Decimal | null, b: Decimal | null): boolean {
  if (a === null || b === null) return a === b;
  return toCents(a) === toCents(b);
}

// --- Months and days ---------------------------------------------------------------------

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

export function isMonth(value: string | null | undefined): value is string {
  return typeof value === "string" && MONTH.test(value);
}

function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

/** Today (YYYY-MM-DD) in the owner's time zone, like the server; else the browser's day. */
export function todayIn(timeZone?: string | null, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(now);
    const get = (type: string) => parts.find((part) => part.type === type)?.value;
    const [year, month, day] = [get("year"), get("month"), get("day")];
    if (year && month && day) return `${year}-${month}-${day}`;
  } catch {
    // Unknown time zone: fall back to the browser's local day.
  }
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function monthOf(date: string): string {
  return date.slice(0, 7);
}

/** "2026-01" shifted by -1 -> "2025-12". */
export function shiftMonth(month: string, delta: number): string {
  const [year = 1970, number = 1] = month.split("-").map(Number);
  const index = year * 12 + (number - 1) + delta;
  return `${pad(Math.floor(index / 12), 4)}-${pad((index % 12) + 1)}`;
}

const monthFormat = new Intl.DateTimeFormat("fr-FR", { month: "long", year: "numeric", timeZone: "UTC" });

/** "2026-10" -> "octobre 2026". */
export function monthLabel(month: string): string {
  if (!isMonth(month)) return month;
  const [year = 1970, number = 1] = month.split("-").map(Number);
  return monthFormat.format(new Date(Date.UTC(year, number - 1, 1)));
}

/** "de septembre 2026", "d'octobre 2026" (elision before a vowel: avril, août, octobre). */
export function ofMonth(month: string): string {
  const label = monthLabel(month);
  return /^[aeiouyàâäéèêëîïôöûüh]/i.test(label) ? `d'${label}` : `de ${label}`;
}

function isCalendarDate(value: string): boolean {
  if (!DAY.test(value)) return false;
  const [year = 0, month = 0, day = 0] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

// --- API paths and query keys --------------------------------------------------------------

export const UNCATEGORISED = "none";
export const PAGE_SIZE = 50;

export interface TransactionFilters {
  /** null = all spaces. */
  spaceId: string | null;
  month: string;
  direction: Direction | "";
  /** "" = every category, UNCATEGORISED = without category, else a category id. */
  category: string;
  q: string;
}

export function transactionsPath(filters: TransactionFilters, cursor?: string | null, limit = PAGE_SIZE): string {
  const params = new URLSearchParams();
  if (filters.spaceId) params.set("space_id", filters.spaceId);
  if (filters.month) params.set("month", filters.month);
  if (filters.direction) params.set("direction", filters.direction);
  if (filters.category === UNCATEGORISED) params.set("uncategorised", "true");
  else if (filters.category) params.set("category_id", filters.category);
  const q = filters.q.trim().slice(0, 200);
  if (q) params.set("q", q);
  params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  return `/v1/finance/transactions?${params.toString()}`;
}

export function categoriesPath(spaceId: string | null, includeArchived: boolean): string {
  const params = new URLSearchParams();
  if (spaceId) params.set("space_id", spaceId);
  if (includeArchived) params.set("include_archived", "true");
  const query = params.toString();
  return query ? `/v1/finance/categories?${query}` : "/v1/finance/categories";
}

export function summaryPath(spaceId: string, month: string): string {
  const params = new URLSearchParams({ space_id: spaceId, month });
  return `/v1/finance/summary?${params.toString()}`;
}

export function proposalPath(documentId: string): string {
  return `/v1/finance/receipts/${encodeURIComponent(documentId)}/proposal`;
}

export function documentHref(documentId: string): string {
  return `/documents?id=${encodeURIComponent(documentId)}`;
}

/** Every finance query starts with "finance". */
export const financeKeys = {
  all: ["finance"] as const,
  categoriesAll: ["finance", "categories"] as const,
  categories: (spaceId: string | null, includeArchived: boolean) =>
    ["finance", "categories", spaceId ?? "all", includeArchived] as const,
  transactionsAll: ["finance", "transactions"] as const,
  transactions: (filters: TransactionFilters) => ["finance", "transactions", filters] as const,
  transaction: (id: string) => ["finance", "transaction", id] as const,
  summaries: ["finance", "summary"] as const,
  summary: (spaceId: string, month: string) => ["finance", "summary", spaceId, month] as const,
  proposals: ["finance", "receipt"] as const,
  proposal: (documentId: string) => ["finance", "receipt", documentId] as const,
  /** Same key as the documents page, so both share the polled document. */
  document: (documentId: string) => ["documents", "detail", documentId] as const,
};

/** To refresh after a transaction is created, changed or deleted (dashboard block too). */
export const TRANSACTION_CHANGE_KEYS: readonly (readonly string[])[] = [
  financeKeys.transactionsAll,
  ["finance", "transaction"],
  financeKeys.summaries,
  financeKeys.proposals,
  ["dashboard"],
];

/** To refresh after a category changes (a deletion uncategorises its transactions). */
export const CATEGORY_CHANGE_KEYS: readonly (readonly string[])[] = [
  financeKeys.categoriesAll,
  financeKeys.transactionsAll,
  financeKeys.summaries,
  financeKeys.proposals,
  ["dashboard"],
];

// --- Errors ------------------------------------------------------------------------------

const FINANCE_MESSAGES: Record<string, string> = {
  category_in_use:
    "Cette catégorie a déjà des opérations : elle ne peut plus passer de dépense à revenu (ni l'inverse).",
  category_kind_mismatch: "Cette catégorie ne correspond pas au sens de l'opération (dépense ou revenu).",
  invalid_cursor: "La liste a changé entre-temps : rechargez la page.",
  idempotency_key_reused: "Cette opération a déjà été envoyée avec d'autres valeurs : réessayez.",
  idempotency_key_in_use: "L'enregistrement précédent est encore en cours : patientez un instant.",
  action_denied: "Action non autorisée dans cet espace.",
};

/** errorMessage() plus the finance codes it does not know yet. */
export function financeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const message = FINANCE_MESSAGES[error.code];
    if (message) return message;
  }
  return errorMessage(error);
}

// --- Labels ------------------------------------------------------------------------------

export const DIRECTION_LABELS: Record<Direction, string> = { expense: "Dépense", income: "Revenu" };
export const KIND_LABELS: Record<CategoryKind, string> = { expense: "Dépenses", income: "Revenus" };
export const SOURCE_LABELS: Record<Transaction["source_type"], string> = {
  manual: "Saisie",
  receipt: "Ticket",
  import: "Import",
};

export function countLabel(count: number, singular: string, plural: string): string {
  return `${count} ${count > 1 ? plural : singular}`;
}

export function transactionTitle(tx: Pick<Transaction, "merchant" | "label" | "source_type">): string {
  return tx.merchant ?? tx.label ?? (tx.source_type === "receipt" ? "Ticket de caisse" : "Opération sans libellé");
}

/** The label under the merchant, when both are set. */
export function transactionSubtitle(tx: Pick<Transaction, "merchant" | "label">): string | null {
  return tx.merchant && tx.label ? tx.label : null;
}

/** Filled automatically by a learned rule, not (yet) confirmed by the owner. */
export function isAutoCategorised(tx: Pick<Transaction, "category_id" | "category_confirmed">): boolean {
  return tx.category_id !== null && !tx.category_confirmed;
}

// --- Categories --------------------------------------------------------------------------

const collator = new Intl.Collator("fr", { sensitivity: "base" });

/** Expenses first, then by name (French order). */
export function sortCategories<T extends Pick<FinanceCategory, "kind" | "name">>(categories: T[]): T[] {
  return [...categories].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "expense" ? -1 : 1;
    return collator.compare(a.name, b.name);
  });
}

/** Categories offered for a transaction: the active ones of its space and kind, plus the
 * current one if it is archived (so that the select still shows it). */
export function categoryChoices(
  categories: FinanceCategory[],
  spaceId: string,
  kind: CategoryKind,
  currentId?: string | null,
): FinanceCategory[] {
  const active = sortCategories(categories.filter((c) => c.space_id === spaceId && c.kind === kind && !c.archived));
  const current = currentId ? categories.find((c) => c.id === currentId) : undefined;
  return current && !active.some((c) => c.id === current.id) ? [...active, current] : active;
}

export function categoryLabel(category: Pick<FinanceCategory, "name" | "archived">, spaceLabel?: string): string {
  const parts = [category.name];
  if (spaceLabel) parts.push(`· ${spaceLabel}`);
  if (category.archived) parts.push("(archivée)");
  return parts.join(" ");
}

export interface Option {
  value: string;
  label: string;
}

/** Category filter of the list; with several spaces, each name says its space. */
export function categoryFilterOptions(
  categories: FinanceCategory[],
  spaceName?: (spaceId: string) => string,
): Option[] {
  return [
    { value: "", label: "Toutes les catégories" },
    { value: UNCATEGORISED, label: "Non classées" },
    ...sortCategories(categories).map((c) => ({
      value: c.id,
      label: categoryLabel(c, spaceName?.(c.space_id) || undefined),
    })),
  ];
}

export const CATEGORY_NAME_MAX = 60;

/** Same cleaning as the server: trimmed, inner whitespace collapsed. */
export function parseCategoryName(raw: string): Parsed<string> {
  const name = raw.split(/\s+/).filter(Boolean).join(" ");
  if (!name) return { ok: false, error: "Donnez un nom à la catégorie." };
  if (name.length > CATEGORY_NAME_MAX) {
    return { ok: false, error: `Le nom ne doit pas dépasser ${CATEGORY_NAME_MAX} caractères.` };
  }
  return { ok: true, value: name };
}

export interface CategoryFormValues {
  name: string;
  kind: CategoryKind;
  budget: string;
}

export const EMPTY_CATEGORY_FORM: CategoryFormValues = { name: "", kind: "expense", budget: "" };

export interface CategoryCreateBody {
  space_id: string;
  name: string;
  kind: CategoryKind;
  monthly_budget: Decimal | null;
}

export function buildCategoryCreate(form: CategoryFormValues, spaceId: string): Parsed<CategoryCreateBody> {
  const name = parseCategoryName(form.name);
  if (!name.ok) return name;
  const budget = parseBudget(form.kind === "expense" ? form.budget : "");
  if (!budget.ok) return { ok: false, error: `Budget : ${budget.error}` };
  return { ok: true, value: { space_id: spaceId, name: name.value, kind: form.kind, monthly_budget: budget.value } };
}

export interface CategoryUpdateBody {
  name?: string;
  /** Explicit null removes the budget. */
  monthly_budget?: Decimal | null;
  archived?: boolean;
}

/** PATCH body with the changed name and budget only (empty: nothing to save). */
export function buildCategoryUpdate(
  category: Pick<FinanceCategory, "name" | "monthly_budget">,
  form: { name: string; budget: string },
): Parsed<CategoryUpdateBody> {
  const name = parseCategoryName(form.name);
  if (!name.ok) return name;
  const budget = parseBudget(form.budget);
  if (!budget.ok) return { ok: false, error: `Budget : ${budget.error}` };
  const body: CategoryUpdateBody = {};
  if (name.value !== category.name) body.name = name.value;
  if (!sameMoney(budget.value, category.monthly_budget)) body.monthly_budget = budget.value;
  return { ok: true, value: body };
}

// --- Budget summary ------------------------------------------------------------------------

/** From this share of the budget on, the category is "close to its budget". */
export const NEAR_BUDGET_RATIO = 0.8;

export type BudgetState = "none" | "ok" | "near" | "over";

export function budgetState(row: Pick<CategorySummary, "budget" | "ratio" | "over_budget">): BudgetState {
  if (row.over_budget) return "over";
  if (row.budget === null) return "none";
  if (row.ratio !== null && row.ratio >= NEAR_BUDGET_RATIO) return "near";
  return "ok";
}

/** Share of the budget used, in percent (over 100 when over budget); null without one. */
export function budgetPercent(ratio: number | null): number | null {
  return ratio === null || !Number.isFinite(ratio) ? null : Math.round(Math.max(0, ratio) * 100);
}

/** Width of a progress bar (0..100). */
export function barWidth(ratio: number | null): number {
  const percent = budgetPercent(ratio);
  return percent === null ? 0 : Math.min(100, percent);
}

export function formatPercent(percent: number): string {
  return `${percent}${NBSP}%`;
}

/** "45,00 € sur 50,00 € (90 %)", or "12,00 € dépensés" without a budget. */
export function spentText(row: Pick<CategorySummary, "spent" | "budget" | "ratio">, currency = "EUR"): string {
  const spent = formatMoney(row.spent, currency);
  if (row.budget === null) return `${spent} dépensés`;
  const percent = budgetPercent(row.ratio);
  const share = percent === null ? "" : ` (${formatPercent(percent)})`;
  return `${spent} sur ${formatMoney(row.budget, currency)}${share}`;
}

/** "Reste 5,00 €" / "Dépassement de 5,00 €"; null without a budget. */
export function remainingText(remaining: Decimal | null, currency = "EUR"): string | null {
  const cents = toCents(remaining);
  if (cents === null) return null;
  if (cents < 0n) return `Dépassement de ${formatCents(-cents, currency)}`;
  return `Reste ${formatCents(cents, currency)}`;
}

/** Expense categories with a budget or spending, income categories that received money. */
export function summaryRows(summary: Pick<BudgetSummary, "categories">): {
  expenses: CategorySummary[];
  incomes: CategorySummary[];
} {
  return {
    expenses: summary.categories.filter((c) => c.kind === "expense" && (c.budget !== null || c.transactions > 0)),
    incomes: summary.categories.filter((c) => c.kind === "income" && c.transactions > 0),
  };
}

export function hasBudget(summary: Pick<BudgetSummary, "totals">): boolean {
  return (toCents(summary.totals.budget) ?? 0n) > 0n;
}

/** Transactions in another currency are neither converted nor counted. */
export function otherCurrencyNote(count: number): string | null {
  if (count <= 0) return null;
  return count === 1
    ? "1 opération dans une autre devise n'est pas comptée."
    : `${count} opérations dans une autre devise ne sont pas comptées.`;
}

// --- Transaction form ------------------------------------------------------------------------

export const MERCHANT_MAX = 120;
export const LABEL_MAX = 200;
export const NOTES_MAX = 2000;
export const CURRENCIES = ["EUR", "USD", "GBP", "CHF", "CAD"] as const;

export interface TransactionFormValues {
  amount: string;
  direction: Direction;
  currency: string;
  tx_date: string;
  merchant: string;
  label: string;
  /** "" = none (on creation, the rule learned for the merchant may apply). */
  category_id: string;
  notes: string;
}

export function emptyTransactionForm(today: string): TransactionFormValues {
  return {
    amount: "",
    direction: "expense",
    currency: "EUR",
    tx_date: today,
    merchant: "",
    label: "",
    category_id: "",
    notes: "",
  };
}

export function transactionToForm(tx: Transaction): TransactionFormValues {
  return {
    amount: decimalToInput(tx.amount),
    direction: tx.direction,
    currency: tx.currency,
    tx_date: tx.tx_date,
    merchant: tx.merchant ?? "",
    label: tx.label ?? "",
    category_id: tx.category_id ?? "",
    notes: tx.notes ?? "",
  };
}

/** What was read on the receipt, as an editable form (the owner checks every value). */
export function proposalToForm(proposal: ReceiptProposal, today: string): TransactionFormValues {
  return {
    ...emptyTransactionForm(today),
    amount: decimalToInput(proposal.total),
    currency: proposal.currency || "EUR",
    tx_date: proposal.tx_date ?? today,
    merchant: proposal.merchant ?? "",
    category_id: proposal.suggested_category?.id ?? "",
  };
}

export type FormField = keyof TransactionFormValues | "space";
export type FieldErrors = Partial<Record<FormField, string>>;
export type FormResult<T> = { ok: true; value: T } | { ok: false; errors: FieldErrors };

/** Order in which the form shows its fields (the first invalid one gets the focus). */
export const FORM_FIELDS: FormField[] = [
  "space",
  "amount",
  "direction",
  "tx_date",
  "currency",
  "merchant",
  "label",
  "category_id",
  "notes",
];

export function firstError(errors: FieldErrors): FormField | null {
  return FORM_FIELDS.find((field) => errors[field]) ?? null;
}

export interface TransactionFields {
  amount: Decimal;
  direction: Direction;
  currency: string;
  tx_date: string;
  merchant: string | null;
  label: string | null;
  notes: string | null;
  category_id: string | null;
}

const line = (value: string): string | null => value.split(/\s+/).filter(Boolean).join(" ") || null;

export function validateTransaction(form: TransactionFormValues, today: string): FormResult<TransactionFields> {
  const errors: FieldErrors = {};
  const amount = parseAmount(form.amount);
  if (!amount.ok) errors.amount = amount.error;
  if (!form.tx_date) errors.tx_date = "Indiquez la date de l'opération.";
  else if (!isCalendarDate(form.tx_date)) errors.tx_date = "Date invalide.";
  else if (form.tx_date > today) errors.tx_date = "La date ne peut pas être dans le futur.";
  if (!/^[A-Z]{3}$/.test(form.currency)) errors.currency = "Devise invalide (code à trois lettres, ex. EUR).";
  const merchant = line(form.merchant);
  if (merchant && merchant.length > MERCHANT_MAX) {
    errors.merchant = `Le commerçant ne doit pas dépasser ${MERCHANT_MAX} caractères.`;
  }
  const label = line(form.label);
  if (label && label.length > LABEL_MAX) errors.label = `Le libellé ne doit pas dépasser ${LABEL_MAX} caractères.`;
  const notes = form.notes.trim() || null;
  if (notes && notes.length > NOTES_MAX) errors.notes = `Les notes ne doivent pas dépasser ${NOTES_MAX} caractères.`;
  if (!amount.ok || Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    value: {
      amount: amount.value,
      direction: form.direction,
      currency: form.currency,
      tx_date: form.tx_date,
      merchant,
      label,
      notes,
      category_id: form.category_id || null,
    },
  };
}

export interface TransactionCreateBody extends TransactionFields {
  space_id: string;
  source_type: "manual" | "receipt";
  document_id?: string;
}

export function buildTransactionCreate(
  form: TransactionFormValues,
  options: { spaceId: string | null; today: string; receiptDocumentId?: string | null },
): FormResult<TransactionCreateBody> {
  const fields = validateTransaction(form, options.today);
  const errors: FieldErrors = fields.ok ? {} : { ...fields.errors };
  if (!options.spaceId) errors.space = "Choisissez un espace.";
  if (!fields.ok || !options.spaceId) return { ok: false, errors };
  const receipt = options.receiptDocumentId;
  return {
    ok: true,
    value: {
      space_id: options.spaceId,
      ...fields.value,
      source_type: receipt ? "receipt" : "manual",
      ...(receipt ? { document_id: receipt } : {}),
    },
  };
}

/** PATCH body with only the changed fields (empty: nothing to save). Setting the category
 * teaches it for the merchant, so it is only sent when the owner changed it. */
export function buildTransactionUpdate(
  tx: Transaction,
  form: TransactionFormValues,
  today: string,
): FormResult<Partial<TransactionFields>> {
  const fields = validateTransaction(form, today);
  if (!fields.ok) return fields;
  const next = fields.value;
  const body: Partial<TransactionFields> = {};
  if (!sameMoney(next.amount, tx.amount)) body.amount = next.amount;
  if (next.direction !== tx.direction) body.direction = next.direction;
  if (next.currency !== tx.currency) body.currency = next.currency;
  if (next.tx_date !== tx.tx_date) body.tx_date = next.tx_date;
  if (next.merchant !== tx.merchant) body.merchant = next.merchant;
  if (next.label !== tx.label) body.label = next.label;
  if (next.notes !== tx.notes) body.notes = next.notes;
  if (next.category_id !== tx.category_id) body.category_id = next.category_id;
  return { ok: true, value: body };
}

export interface PendingKey {
  body: string;
  key: string;
}

/**
 * Idempotency-Key of a creation: the same key while the body is unchanged (a retry after a
 * network error is deduplicated by the server), a new one as soon as it changes (the server
 * refuses a key reused with another body).
 */
export function idempotencyKeyFor(previous: PendingKey | null, body: unknown, makeKey: () => string): PendingKey {
  const serialised = JSON.stringify(body);
  return previous && previous.body === serialised ? previous : { body: serialised, key: makeKey() };
}

// --- Receipts (UC02) ---------------------------------------------------------------------

/** Under this confidence, the owner is asked to check every value carefully. */
export const LOW_CONFIDENCE = 0.6;
export const HIGH_CONFIDENCE = 0.85;
export const POLL_MS = 2_000;

export function confidencePercent(confidence: number): number {
  return Math.round(Math.min(1, Math.max(0, Number.isFinite(confidence) ? confidence : 0)) * 100);
}

/** 0.75 -> "confiance 75 %". */
export function confidenceLabel(confidence: number): string {
  return `confiance ${formatPercent(confidencePercent(confidence))}`;
}

export function confidenceTone(confidence: number): Tone {
  if (confidence >= HIGH_CONFIDENCE) return "teal";
  return confidence >= LOW_CONFIDENCE ? "amber" : "red";
}

export function suggestionHint(suggested: ReceiptProposal["suggested_category"]): string | null {
  if (!suggested) return null;
  return suggested.source === "rule"
    ? `« ${suggested.name} » est suggérée d'après vos choix précédents pour ce commerçant.`
    : `« ${suggested.name} » est suggérée d'après le nom du commerçant : vérifiez-la.`;
}

type ReadingState = Pick<DocumentSummary, "status" | "error">;

export function isReading(document: ReadingState | undefined): boolean {
  return document?.status === "pending" || document?.status === "processing";
}

/** Poll while the document is read; slower when the server postponed the reading. */
export function readingPollInterval(document: ReadingState | undefined, base = POLL_MS): number | false {
  if (!isReading(document)) return false;
  return document?.status === "pending" && document.error ? base * 5 : base;
}

const READING_ERRORS: Record<string, string> = {
  unreadable: "Le ticket est illisible : aucun texte n'a pu en être tiré.",
  too_many_pages: "Le document compte trop de pages pour être lu.",
  encrypted: "PDF protégé par mot de passe : retirez la protection, puis envoyez-le à nouveau.",
  too_slow: "La lecture a pris trop de temps et a été interrompue.",
  image_too_large: "L'image est trop grande (en pixels) pour être lue.",
  original_purged: "L'original n'est plus conservé : le ticket ne peut plus être lu.",
  original_unreadable: "L'original chiffré n'a pas pu être déchiffré.",
  processing_error: "Erreur inattendue pendant la lecture.",
  extraction_unavailable: "La lecture locale est indisponible pour le moment : nouvel essai automatique dans quelques minutes.",
  documents_unavailable:
    "Le stockage des documents est indisponible : nouvel essai automatique dans quelques minutes.",
  ocr_unavailable:
    "La reconnaissance de texte est indisponible pour le moment : nouvel essai automatique dans quelques minutes.",
};

export function readingErrorMessage(code: string | null): string {
  if (!code) return "La lecture a échoué.";
  return READING_ERRORS[code] ?? `La lecture a échoué (code « ${code} »).`;
}

/** Why a receipt cannot be read (failed, rejected or not a receipt); null otherwise. */
export function receiptFailure(document: Pick<DocumentSummary, "status" | "error" | "purpose">): string | null {
  if (document.purpose !== "receipt") return "Ce document n'est pas un ticket de caisse.";
  if (document.status === "failed") return readingErrorMessage(document.error);
  if (document.status === "rejected") return "Ce fichier a été refusé : il ne peut pas être lu.";
  return null;
}

/** The reading was postponed by the server (pending with a reason): it will retry. */
export function readingDelay(document: ReadingState): string | null {
  return document.status === "pending" && document.error ? readingErrorMessage(document.error) : null;
}
