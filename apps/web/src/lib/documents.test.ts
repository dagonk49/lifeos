import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  buildUploadForm,
  canEditText,
  detailPollInterval,
  documentPath,
  documentsPath,
  emptyListMessage,
  errorExplanation,
  financesReceiptHref,
  formatConfidence,
  formatFileSize,
  formatMoney,
  isLowConfidence,
  listPollInterval,
  originalLabel,
  parseTitle,
  readReceipt,
  retentionHint,
  sensitivityDetails,
  sourceLink,
  statusInfo,
  textSourceLabel,
  uploadErrorMessage,
  validateText,
  type UploadValues,
} from "./documents";
import { formatDate } from "./format";

const NBSP = " ";
const NNBSP = " ";

describe("documentsPath", () => {
  it("adds only the filters that are set", () => {
    expect(documentsPath({ spaceId: null, purpose: "all", status: "all" })).toBe("/v1/documents?limit=30");
    expect(documentsPath({ spaceId: "s 1", purpose: "receipt", status: "failed", cursor: "abc=", limit: 10 })).toBe(
      "/v1/documents?space_id=s+1&purpose=receipt&status=failed&limit=10&cursor=abc%3D",
    );
  });

  it("encodes document ids", () => {
    expect(documentPath("a/b", "/reprocess")).toBe("/v1/documents/a%2Fb/reprocess");
  });

  it("explains an empty list according to the filters", () => {
    expect(emptyListMessage("all", "all")).toMatch(/^Aucun document pour l'instant/);
    expect(emptyListMessage("receipt", "all")).toBe("Aucun document ne correspond à ces filtres.");
  });
});

describe("polling", () => {
  it("polls a document while it is being read", () => {
    expect(detailPollInterval("pending")).toBe(2000);
    expect(detailPollInterval("processing")).toBe(2000);
    expect(detailPollInterval("ready")).toBe(false);
    expect(detailPollInterval("failed")).toBe(false);
    expect(detailPollInterval("rejected")).toBe(false);
    expect(detailPollInterval(undefined)).toBe(false);
  });

  it("polls a list only while one of its documents is being read", () => {
    expect(listPollInterval([{ status: "ready" }, { status: "processing" }])).toBe(5000);
    expect(listPollInterval([{ status: "ready" }, { status: "failed" }])).toBe(false);
    expect(listPollInterval(undefined)).toBe(false);
  });

  it("allows corrections once the reading is over", () => {
    expect(canEditText("ready")).toBe(true);
    expect(canEditText("failed")).toBe(true);
    expect(canEditText("pending")).toBe(false);
    expect(canEditText("processing")).toBe(false);
  });
});

describe("status and error explanations", () => {
  it("explains every failure code in French", () => {
    expect(errorExplanation("unreadable")).toMatch(/illisible/);
    expect(errorExplanation("encrypted")).toMatch(/^PDF protégé par mot de passe/);
    expect(errorExplanation("too_many_pages")).toMatch(/trop de pages/);
    expect(errorExplanation("too_slow")).toMatch(/trop de temps/);
    expect(errorExplanation("image_too_large")).toMatch(/image est trop grande/);
    expect(errorExplanation("unsupported_type")).toMatch(/type de fichier/);
    expect(errorExplanation("too_large")).toMatch(/trop volumineux/);
    expect(errorExplanation("original_purged")).toMatch(/plus conservé/);
    expect(errorExplanation("original_unreadable")).toMatch(/déchiffré/);
    expect(errorExplanation("processing_error")).toMatch(/inattendue/);
    expect(errorExplanation("extraction_unavailable")).toMatch(/^Lecture locale indisponible.*nouvel essai automatique/);
    expect(errorExplanation("documents_unavailable")).toMatch(/stockage/);
    expect(errorExplanation("ocr_unavailable")).toMatch(/OCR/);
  });

  it("names unknown codes instead of hiding them", () => {
    expect(errorExplanation("weird")).toBe("La lecture a échoué (code « weird »).");
    expect(errorExplanation(null)).toBeNull();
  });

  it("describes each processing state", () => {
    expect(statusInfo({ status: "ready", error: null })).toBeNull();
    expect(statusInfo({ status: "pending", error: null })).toEqual({
      tone: "info",
      text: "En attente de lecture : le document sera lu dans quelques instants.",
    });
    expect(statusInfo({ status: "pending", error: "extraction_unavailable" })?.tone).toBe("warning");
    expect(statusInfo({ status: "pending", error: "extraction_unavailable" })?.text).toMatch(/nouvel essai automatique/);
    expect(statusInfo({ status: "pending", error: "other" })?.text).toMatch(/^Lecture reportée/);
    expect(statusInfo({ status: "processing", error: null })?.tone).toBe("info");
    expect(statusInfo({ status: "failed", error: "encrypted" })).toEqual({
      tone: "error",
      text: "PDF protégé par mot de passe : retirez la protection, puis envoyez-le à nouveau.",
    });
    expect(statusInfo({ status: "failed", error: null })?.text).toBe("La lecture a échoué.");
    expect(statusInfo({ status: "rejected", error: null })?.text).toBe("Document refusé.");
    expect(statusInfo({ status: "rejected", error: "unsupported_type" })?.text).toMatch(/^Document refusé\. Ce type/);
  });
});

describe("extracted text", () => {
  it("labels the text source with the OCR confidence", () => {
    expect(textSourceLabel("ocr", 0.84)).toBe(`Lu par OCR, confiance 84${NBSP}%`);
    expect(textSourceLabel("ocr", null)).toBe("Lu par OCR");
    expect(textSourceLabel("pdf", null)).toBe("Texte du PDF");
    expect(textSourceLabel("text", null)).toBe("Fichier texte");
    expect(textSourceLabel(null, null)).toBeNull();
  });

  it("flags low confidence below 60 %", () => {
    expect(isLowConfidence(0.59)).toBe(true);
    expect(isLowConfidence(0.6)).toBe(false);
    expect(isLowConfidence(null)).toBe(false);
    expect(formatConfidence(1.2)).toBe(`100${NBSP}%`);
    expect(formatConfidence(0.005)).toBe(`1${NBSP}%`);
    expect(formatConfidence(null)).toBe("");
  });

  it("limits the text length like the API", () => {
    expect(validateText("abc")).toBeNull();
    expect(validateText("a".repeat(200_001))).toMatch(/trop long/);
  });

  it("normalises a new title and detects no-ops", () => {
    expect(parseTitle("  Facture   EDF ", "Facture")).toEqual({ ok: true, value: "Facture EDF" });
    expect(parseTitle("Facture ", "Facture")).toEqual({ ok: true, value: null });
    expect(parseTitle("   ", "Facture")).toEqual({ ok: false, error: "Le titre ne peut pas être vide." });
    expect(parseTitle("x".repeat(201), "Facture").ok).toBe(false);
  });
});

describe("formatFileSize", () => {
  it("formats sizes the French way", () => {
    expect(formatFileSize(0)).toBe(`0${NBSP}o`);
    expect(formatFileSize(512)).toBe(`512${NBSP}o`);
    expect(formatFileSize(1024)).toBe(`1${NBSP}Ko`);
    expect(formatFileSize(1536)).toBe(`1,5${NBSP}Ko`);
    expect(formatFileSize(1.2 * 1024 * 1024)).toBe(`1,2${NBSP}Mo`);
    expect(formatFileSize(20 * 1024 * 1024)).toBe(`20${NBSP}Mo`);
    expect(formatFileSize(150 * 1024)).toBe(`150${NBSP}Ko`);
    expect(formatFileSize(3 * 1024 ** 3)).toBe(`3${NBSP}Go`);
  });

  it("never shows 1 024 of a unit", () => {
    expect(formatFileSize(1024 * 1024 - 10)).toBe(`1${NBSP}Mo`);
  });

  it("returns an empty string for invalid sizes", () => {
    expect(formatFileSize(-1)).toBe("");
    expect(formatFileSize(Number.NaN)).toBe("");
  });
});

describe("formatMoney", () => {
  it("formats decimal strings without float arithmetic", () => {
    expect(formatMoney("1234.5")).toBe(`1${NNBSP}234,50${NBSP}€`);
    expect(formatMoney("6.43")).toBe(`6,43${NBSP}€`);
    expect(formatMoney("0.1")).toBe(`0,10${NBSP}€`);
    expect(formatMoney("12")).toBe(`12,00${NBSP}€`);
    expect(formatMoney("-1000000.00")).toBe(`-1${NNBSP}000${NNBSP}000,00${NBSP}€`);
    expect(formatMoney("12.50", "USD")).toBe(`12,50${NBSP}$`);
    expect(formatMoney("12.50", "CHF")).toBe(`12,50${NBSP}CHF`);
  });

  it("keeps precision beyond what a float holds", () => {
    expect(formatMoney("90071992547409931.99")).toBe(
      `90${NNBSP}071${NNBSP}992${NNBSP}547${NNBSP}409${NNBSP}931,99${NBSP}€`,
    );
  });

  it("rounds extra decimals half away from zero", () => {
    expect(formatMoney("1.005")).toBe(`1,01${NBSP}€`);
    expect(formatMoney("1.004")).toBe(`1,00${NBSP}€`);
    expect(formatMoney("-2.675")).toBe(`-2,68${NBSP}€`);
    expect(formatMoney("-0.001")).toBe(`0,00${NBSP}€`);
  });

  it("leaves unparseable values alone", () => {
    expect(formatMoney(null)).toBe("");
    expect(formatMoney("abc")).toBe("abc");
  });
});

describe("retention", () => {
  it("labels the original's retention", () => {
    expect(originalLabel({ original_available: false, original_until: null })).toBe("Original supprimé");
    expect(originalLabel({ original_available: true, original_until: null })).toBe("Original conservé sans limite");
    expect(originalLabel({ original_available: true, original_until: "2027-01-10T08:00:00Z" })).toBe(
      `Original conservé jusqu'au ${formatDate("2027-01-10T08:00:00Z")}`,
    );
  });

  it("explains what happens to the original", () => {
    expect(retentionHint({ original_available: true, original_until: "2027-01-10T08:00:00Z" })).toMatch(
      /^L'original chiffré sera supprimé le .* ; le texte extrait reste disponible\.$/,
    );
    expect(retentionHint({ original_available: true, original_until: null })).toMatch(/conservé jusqu'à ce que/);
    expect(retentionHint({ original_available: false, original_until: null })).toMatch(/n'est plus conservé/);
  });
});

describe("analysis", () => {
  it("lists sensitivity categories with their reasons", () => {
    expect(
      sensitivityDetails({
        sensitivity: ["bank", "identity"],
        analysis: { sensitivity_reasons: { bank: ["IBAN", "RIB"] } },
      }),
    ).toEqual([
      { category: "bank", label: "Banque", reasons: ["IBAN", "RIB"] },
      { category: "identity", label: "Identité", reasons: [] },
    ]);
    expect(sensitivityDetails({ sensitivity: [], analysis: null })).toEqual([]);
  });

  it("reads a stored receipt", () => {
    const reading = readReceipt({
      receipt: {
        merchant: "CARREFOUR",
        tx_date: "2026-10-10",
        total: "6.43",
        currency: "EUR",
        vat_rates: ["5.5"],
        card_payment: true,
        confidence: 0.82,
        warnings: ["Date non trouvée."],
        lines: { merchant: "CARREFOUR MARKET", tx_date: null, total: "TOTAL 6,43" },
      },
    });
    expect(reading).toEqual({
      merchant: "CARREFOUR",
      txDate: "2026-10-10",
      total: "6.43",
      currency: "EUR",
      confidence: 0.82,
      cardPayment: true,
      warnings: ["Date non trouvée."],
      lines: { merchant: "CARREFOUR MARKET", txDate: null, total: "TOTAL 6,43" },
    });
  });

  it("reads a malformed receipt defensively", () => {
    expect(readReceipt(null)).toBeNull();
    expect(readReceipt({})).toBeNull();
    const reading = readReceipt({ receipt: { total: "6,43", currency: "euro", warnings: [1, "ok"], lines: "x" } as never });
    expect(reading).toMatchObject({
      merchant: null,
      total: null,
      currency: "EUR",
      confidence: null,
      cardPayment: false,
      warnings: ["ok"],
      lines: { merchant: null, txDate: null, total: null },
    });
  });

  it("links to the finances page and to the document's origin", () => {
    expect(financesReceiptHref("d 1")).toBe("/finances?receipt=d%201");
    expect(sourceLink("goal", "g1")).toEqual({ label: "Objectif", href: "/goals?id=g1" });
    expect(sourceLink("journal_entry", "j1")).toEqual({ label: "Entrée de journal", href: "/journal" });
    expect(sourceLink(null, "x")).toBeNull();
    expect(sourceLink("task", null)).toBeNull();
  });
});

describe("upload", () => {
  const file = new File(["%PDF-1.7 hello"], "facture.pdf", { type: "application/pdf" });
  const base: UploadValues = { file, spaceId: "s1", purpose: "receipt", title: "", keepOriginal: false };

  it("builds the multipart body", () => {
    const result = buildUploadForm({ ...base, title: "  Ticket   Lidl ", keepOriginal: true });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const form = result.value;
    expect(form.get("space_id")).toBe("s1");
    expect(form.get("purpose")).toBe("receipt");
    expect(form.get("title")).toBe("Ticket Lidl");
    expect(form.get("keep_original")).toBe("true");
    expect((form.get("file") as File).name).toBe("facture.pdf");
  });

  it("omits an empty title (the server uses the file name)", () => {
    const result = buildUploadForm(base);
    expect(result.ok && result.value.has("title")).toBe(false);
    expect(result.ok && result.value.get("keep_original")).toBe("false");
  });

  it("rejects what cannot be sent", () => {
    expect(buildUploadForm({ ...base, file: null })).toEqual({ ok: false, error: "Choisissez un fichier à envoyer." });
    expect(buildUploadForm({ ...base, file: new File([], "vide.txt") })).toEqual({
      ok: false,
      error: "Le fichier est vide.",
    });
    expect(buildUploadForm({ ...base, spaceId: "" })).toEqual({ ok: false, error: "Choisissez un espace." });
    expect(buildUploadForm({ ...base, title: "x".repeat(201) }).ok).toBe(false);
  });

  it("gives the server's size limit when a file is too large", () => {
    const tooLarge = new ApiError(413, {
      title: "Too large",
      status: 413,
      code: "document_too_large",
      max_bytes: 20 * 1024 * 1024,
    });
    expect(uploadErrorMessage(tooLarge)).toBe(`Le fichier est trop volumineux (20${NBSP}Mo au maximum).`);
    const unsupported = new ApiError(415, { title: "No", status: 415, code: "unsupported_type" });
    expect(uploadErrorMessage(unsupported)).toMatch(/^Type de fichier refusé/);
    expect(uploadErrorMessage(new ApiError(413, { title: "x", status: 413, code: "document_too_large" }))).toBe(
      "Le fichier est trop volumineux.",
    );
  });
});
