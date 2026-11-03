// Pure helpers for the bank statement import (task 25): CSV file -> preview -> validation.
//
// Nothing reaches the budget before « Importer » : the server keeps a preview, and these
// helpers only describe it (labels, choices, exact totals in BigInt cents).

import { countLabel, formatCents, toCents } from "./finance";
import type { DateFormat, FinanceImport, ImportMapping, ImportRow, ImportRowStatus } from "./types";

type Tone = "neutral" | "teal" | "amber" | "red" | "blue" | "violet";

export const IMPORT_ACCEPT = ".csv,text/csv,text/plain";

export function importPath(id?: string): string {
  return id ? `/v1/finance/imports/${encodeURIComponent(id)}` : "/v1/finance/imports";
}

export function importsListPath(spaceId: string | null): string {
  return spaceId ? `/v1/finance/imports?${new URLSearchParams({ space_id: spaceId }).toString()}` : "/v1/finance/imports";
}

export function importRowsPath(id: string, cursor?: string | null, limit = 200): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return `${importPath(id)}/rows?${params.toString()}`;
}

// --- Labels ------------------------------------------------------------------------------

/** Every status says itself in words (never by colour alone). */
export const ROW_STATUS_LABELS: Record<ImportRowStatus, string> = {
  pending: "À lire",
  valid: "Nouvelle",
  invalid: "Illisible",
  in_file_duplicate: "En double dans le fichier",
  already_imported: "Déjà importée",
  possible_duplicate: "Doublon possible",
};

export const ROW_STATUS_TONES: Record<ImportRowStatus, Tone> = {
  pending: "neutral",
  valid: "teal",
  invalid: "red",
  in_file_duplicate: "neutral",
  already_imported: "neutral",
  possible_duplicate: "amber",
};

const REASONS: Record<string, string> = {
  date_invalid: "date illisible avec le format choisi",
  date_in_future: "date dans le futur",
  amount_invalid: "montant illisible",
  amount_precision: "plus de deux décimales (jamais arrondi)",
  amount_too_large: "montant trop élevé",
  amount_zero: "montant nul",
  amount_missing: "aucun montant",
  amount_ambiguous: "débit et crédit remplis tous les deux",
  currency_invalid: "devise inconnue",
  column_count: "nombre de colonnes différent de l'en-tête",
};

export function reasonLabel(reason: string | null): string {
  if (!reason) return "";
  return REASONS[reason] ?? `refusée (${reason})`;
}

export const IMPORT_STATUS_LABELS: Record<FinanceImport["status"], string> = {
  preview: "Aperçu (rien d'enregistré)",
  committed: "Importé",
  undone: "Annulé",
};

export const ENCODING_LABELS: Record<FinanceImport["encoding"], string> = {
  "utf-8": "UTF-8",
  cp1252: "Windows-1252 (Excel, banques françaises)",
};

export const DATE_FORMATS: DateFormat[] = ["DD/MM/YYYY", "DD/MM/YY", "YYYY-MM-DD", "DD.MM.YYYY", "DD-MM-YYYY"];

export const DATE_FORMAT_LABELS: Record<DateFormat, string> = {
  "DD/MM/YYYY": "31/12/2026",
  "DD/MM/YY": "31/12/26",
  "YYYY-MM-DD": "2026-12-31",
  "DD.MM.YYYY": "31.12.2026",
  "DD-MM-YYYY": "31-12-2026",
};

// --- Mapping ------------------------------------------------------------------------------

export interface MappingForm {
  date_column: string;
  date_format: DateFormat;
  label_column: string;
  amount_mode: ImportMapping["amount_mode"];
  amount_column: string;
  debit_column: string;
  credit_column: string;
  decimal_separator: ImportMapping["decimal_separator"];
  reference_column: string;
}

const column = (value: number | null | undefined): string => (value === null || value === undefined ? "" : String(value));

export function mappingToForm(mapping: ImportMapping | null): MappingForm {
  return {
    date_column: column(mapping?.date_column),
    date_format: mapping?.date_format ?? "DD/MM/YYYY",
    label_column: column(mapping?.label_column),
    amount_mode: mapping?.amount_mode ?? "signed",
    amount_column: column(mapping?.amount_column),
    debit_column: column(mapping?.debit_column),
    credit_column: column(mapping?.credit_column),
    decimal_separator: mapping?.decimal_separator ?? ",",
    reference_column: column(mapping?.reference_column),
  };
}

export type MappingResult = { ok: true; value: ImportMapping } | { ok: false; error: string };

/** The form as an API body; every needed column chosen, never the same one twice. */
export function buildMapping(form: MappingForm, columns: number): MappingResult {
  const pick = (value: string): number | null => {
    if (value === "") return null;
    const index = Number.parseInt(value, 10);
    return Number.isInteger(index) && index >= 0 && index < columns ? index : null;
  };
  const date = pick(form.date_column);
  const label = pick(form.label_column);
  if (date === null) return { ok: false, error: "Choisissez la colonne de la date." };
  if (label === null) return { ok: false, error: "Choisissez la colonne du libellé." };
  const amount = form.amount_mode === "signed" ? pick(form.amount_column) : null;
  const debit = form.amount_mode === "debit_credit" ? pick(form.debit_column) : null;
  const credit = form.amount_mode === "debit_credit" ? pick(form.credit_column) : null;
  if (form.amount_mode === "signed" && amount === null) return { ok: false, error: "Choisissez la colonne du montant." };
  if (form.amount_mode === "debit_credit" && (debit === null || credit === null)) {
    return { ok: false, error: "Choisissez les colonnes du débit et du crédit." };
  }
  const reference = pick(form.reference_column);
  const used = [date, label, amount, debit, credit, reference].filter((c): c is number => c !== null);
  if (new Set(used).size !== used.length) return { ok: false, error: "Une même colonne est choisie deux fois." };
  return {
    ok: true,
    value: {
      date_column: date,
      date_format: form.date_format,
      label_column: label,
      amount_mode: form.amount_mode,
      amount_column: amount,
      debit_column: debit,
      credit_column: credit,
      decimal_separator: form.decimal_separator,
      reference_column: reference,
      currency_column: null,
      currency: "EUR",
    },
  };
}

// --- Selection and totals -------------------------------------------------------------------

/** Imported by default: new operations only. A possible duplicate waits for the owner. */
export function selectedByDefault(row: Pick<ImportRow, "status">): boolean {
  return row.status === "valid";
}

export function isSelectable(row: Pick<ImportRow, "status">): boolean {
  return row.status === "valid" || row.status === "possible_duplicate";
}

/** The commit body: the owner's departures from the default choice. */
export function commitBody(
  rows: Pick<ImportRow, "line_no" | "status">[],
  selected: ReadonlySet<number>,
): { exclude_lines: number[]; include_lines: number[] } {
  const exclude_lines: number[] = [];
  const include_lines: number[] = [];
  for (const row of rows) {
    if (!isSelectable(row)) continue;
    const chosen = selected.has(row.line_no);
    if (selectedByDefault(row) && !chosen) exclude_lines.push(row.line_no);
    if (!selectedByDefault(row) && chosen) include_lines.push(row.line_no);
  }
  return { exclude_lines, include_lines };
}

export interface SelectionTotals {
  count: number;
  expenses: bigint;
  income: bigint;
}

/** Exact totals of the chosen rows (BigInt cents, never floats). */
export function selectionTotals(
  rows: Pick<ImportRow, "line_no" | "status" | "amount" | "direction">[],
  selected: ReadonlySet<number>,
): SelectionTotals {
  const totals: SelectionTotals = { count: 0, expenses: 0n, income: 0n };
  for (const row of rows) {
    if (!isSelectable(row) || !selected.has(row.line_no)) continue;
    const cents = toCents(row.amount) ?? 0n;
    totals.count += 1;
    if (row.direction === "expense") totals.expenses += cents;
    else totals.income += cents;
  }
  return totals;
}

export function totalsText(totals: SelectionTotals, currency = "EUR"): string {
  return `${countLabel(totals.count, "opération", "opérations")} : ${formatCents(totals.expenses, currency)} de dépenses, ${formatCents(totals.income, currency)} de revenus`;
}

export function importButtonLabel(count: number): string {
  return count === 0 ? "Aucune opération à importer" : `Importer ${countLabel(count, "opération", "opérations")}`;
}

/** « 3 nouvelles opérations importées », « 0 nouvelle opération » when all were known. */
export function resultText(finance: Pick<FinanceImport, "imported_count" | "skipped_count">): string {
  const added = finance.imported_count;
  const head = added > 1 ? `${added} nouvelles opérations importées` : `${added} nouvelle opération importée`;
  if (finance.skipped_count === 0) return `${head}.`;
  return `${head}, ${countLabel(finance.skipped_count, "ligne écartée", "lignes écartées")} (déjà importées, doublons ou illisibles).`;
}

export function countsSummary(counts: FinanceImport["counts"]): { status: ImportRowStatus; label: string; count: number }[] {
  const order: ImportRowStatus[] = ["valid", "possible_duplicate", "already_imported", "in_file_duplicate", "invalid"];
  return order
    .map((status) => ({ status, label: ROW_STATUS_LABELS[status], count: counts[status] ?? 0 }))
    .filter((entry) => entry.count > 0);
}
