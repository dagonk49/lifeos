import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { financeErrorMessage } from "./finance";
import {
  buildMapping,
  commitBody,
  countsSummary,
  importButtonLabel,
  importRowsPath,
  importsListPath,
  mappingToForm,
  reasonLabel,
  resultText,
  ROW_STATUS_LABELS,
  selectedByDefault,
  selectionTotals,
  totalsText,
} from "./financeImport";
import type { ImportRow, ImportRowStatus } from "./types";

function row(line_no: number, status: ImportRowStatus, amount: string | null, direction: "expense" | "income" = "expense"): ImportRow {
  return {
    line_no,
    status,
    reason: null,
    cells: [],
    tx_date: "2026-09-01",
    amount,
    direction,
    currency: "EUR",
    label: null,
    merchant: null,
    category_id: null,
    match_id: null,
    match_date: null,
  };
}

describe("financeImport", () => {
  it("builds paths", () => {
    expect(importsListPath("s1")).toBe("/v1/finance/imports?space_id=s1");
    expect(importsListPath(null)).toBe("/v1/finance/imports");
    expect(importRowsPath("i/1", "12")).toBe("/v1/finance/imports/i%2F1/rows?limit=200&cursor=12");
  });

  it("validates the column mapping", () => {
    const form = mappingToForm({
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
    });
    expect(form.amount_column).toBe("2");
    const built = buildMapping(form, 3);
    expect(built.ok && built.value).toMatchObject({ date_column: 0, label_column: 1, amount_column: 2 });
    expect(buildMapping({ ...form, amount_column: "" }, 3)).toEqual({
      ok: false,
      error: "Choisissez la colonne du montant.",
    });
    expect(buildMapping({ ...form, label_column: "0" }, 3)).toEqual({
      ok: false,
      error: "Une même colonne est choisie deux fois.",
    });
    expect(buildMapping({ ...form, amount_column: "7" }, 3).ok).toBe(false);
    const split = buildMapping({ ...form, amount_mode: "debit_credit", debit_column: "2", credit_column: "" }, 4);
    expect(split).toEqual({ ok: false, error: "Choisissez les colonnes du débit et du crédit." });
  });

  it("imports new rows by default and possible duplicates only on request", () => {
    const rows = [row(2, "valid", "45.30"), row(3, "possible_duplicate", "2.50"), row(4, "already_imported", "9.00"), row(5, "valid", "2000.00", "income")];
    expect(rows.map(selectedByDefault)).toEqual([true, false, false, true]);
    const selected = new Set([3, 5]);
    expect(commitBody(rows, selected)).toEqual({ exclude_lines: [2], include_lines: [3] });
    const totals = selectionTotals(rows, selected);
    expect(totals).toEqual({ count: 2, expenses: 250n, income: 200000n });
    expect(totalsText(totals)).toBe("2 opérations : 2,50 € de dépenses, 2 000,00 € de revenus");
  });

  it("is exact on large sums (BigInt cents)", () => {
    const rows = [row(2, "valid", "999999999999.99"), row(3, "valid", "0.01")];
    expect(selectionTotals(rows, new Set([2, 3])).expenses).toBe(100000000000000n);
  });

  it("says statuses, reasons and results in French", () => {
    expect(ROW_STATUS_LABELS.possible_duplicate).toBe("Doublon possible");
    expect(reasonLabel("amount_precision")).toBe("plus de deux décimales (jamais arrondi)");
    expect(reasonLabel("strange")).toBe("refusée (strange)");
    expect(importButtonLabel(1)).toBe("Importer 1 opération");
    expect(importButtonLabel(0)).toBe("Aucune opération à importer");
    expect(resultText({ imported_count: 0, skipped_count: 5 })).toBe(
      "0 nouvelle opération importée, 5 lignes écartées (déjà importées, doublons ou illisibles).",
    );
    expect(resultText({ imported_count: 3, skipped_count: 0 })).toBe("3 nouvelles opérations importées.");
    expect(countsSummary({ invalid: 1, valid: 4 }).map((c) => c.label)).toEqual(["Nouvelle", "Illisible"]);
    expect(financeErrorMessage(new ApiError(409, { title: "x", status: 409, code: "import_not_in_preview" }))).toBe(
      "Cet import a déjà été validé ou annulé.",
    );
  });
});
