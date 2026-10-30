// Pure helpers for the documents page (F10): labels, sizes, money, statuses, upload form.

import { errorMessage, isApiError } from "./api";
import { formatDate } from "./format";
import type { Parsed } from "./goals";
import type {
  Decimal,
  DocumentAnalysis,
  DocumentKind,
  DocumentPurpose,
  DocumentSourceType,
  DocumentStatus,
  DocumentSummary,
  SensitivityCategory,
} from "./types";

type Tone = "neutral" | "teal" | "amber" | "red" | "blue" | "violet";

const NBSP = " ";
// Thousands separator used by Intl for fr-FR.
const NNBSP = " ";

// --- Labels ----------------------------------------------------------------------------

export const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: "En attente",
  processing: "Lecture en cours",
  ready: "Prêt",
  failed: "Échec",
  rejected: "Refusé",
};

export const DOCUMENT_STATUS_TONES: Record<DocumentStatus, Tone> = {
  pending: "neutral",
  processing: "blue",
  ready: "teal",
  failed: "red",
  rejected: "red",
};

export const PURPOSE_LABELS: Record<DocumentPurpose, string> = {
  document: "Document",
  receipt: "Ticket de caisse",
  attachment: "Pièce jointe",
  dictation: "Dictée",
};

export const KIND_LABELS: Record<DocumentKind, string> = {
  image: "Image",
  pdf: "PDF",
  text: "Texte",
  audio: "Audio",
};

export const SENSITIVITY_LABELS: Record<SensitivityCategory, string> = {
  identity: "Identité",
  health: "Santé",
  bank: "Banque",
};

export const SOURCE_TYPE_LABELS: Record<DocumentSourceType, string> = {
  journal_entry: "Entrée de journal",
  message: "Message de l'assistant",
  conversation: "Conversation",
  task: "Tâche",
  goal: "Objectif",
  transaction: "Opération",
  mail_message: "E-mail",
};

// --- Filters and queries ---------------------------------------------------------------

/** Dictations are transient (voice module): never offered as a filter. */
export type PurposeFilter = "all" | "document" | "receipt" | "attachment";
export type StatusFilter = "all" | DocumentStatus;

export const PURPOSE_FILTERS: { value: PurposeFilter; label: string }[] = [
  { value: "all", label: "Tous" },
  { value: "document", label: "Documents" },
  { value: "receipt", label: "Tickets" },
  { value: "attachment", label: "Pièces jointes" },
];

export const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "Tous les statuts" },
  { value: "ready", label: "Prêts" },
  { value: "pending", label: "En attente" },
  { value: "processing", label: "Lecture en cours" },
  { value: "failed", label: "En échec" },
  { value: "rejected", label: "Refusés" },
];

export const PAGE_SIZE = 30;

export interface ListParams {
  spaceId: string | null;
  purpose: PurposeFilter;
  status: StatusFilter;
  cursor?: string | null;
  limit?: number;
}

/** `GET /v1/documents` path for a space (null = all spaces), filters and a page cursor. */
export function documentsPath({ spaceId, purpose, status, cursor, limit = PAGE_SIZE }: ListParams): string {
  const params = new URLSearchParams();
  if (spaceId) params.set("space_id", spaceId);
  if (purpose !== "all") params.set("purpose", purpose);
  if (status !== "all") params.set("status", status);
  params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  return `/v1/documents?${params.toString()}`;
}

export function documentPath(id: string, suffix = ""): string {
  return `/v1/documents/${encodeURIComponent(id)}${suffix}`;
}

export function emptyListMessage(purpose: PurposeFilter, status: StatusFilter): string {
  if (purpose === "all" && status === "all") {
    return "Aucun document pour l'instant. Envoyez un PDF, une photo ou un fichier texte.";
  }
  return "Aucun document ne correspond à ces filtres.";
}

/** Every document query starts with "documents": invalidating it refreshes lists and details. */
export const documentKeys = {
  all: ["documents"] as const,
  lists: ["documents", "list"] as const,
  list: (spaceId: string | null, purpose: PurposeFilter, status: StatusFilter) =>
    ["documents", "list", spaceId, purpose, status] as const,
  detail: (id: string) => ["documents", "detail", id] as const,
  /** The Finances page's proposal read from a receipt (`financeKeys.proposal` in lib/finance):
   * stale once the text is corrected or read again. */
  receiptProposal: (id: string) => ["finance", "receipt", id] as const,
};

// --- Processing status -----------------------------------------------------------------

/** Pending or processing: the server is (or will be) reading the document. */
export function isInProgress(status: DocumentStatus | undefined): boolean {
  return status === "pending" || status === "processing";
}

export const DETAIL_POLL_MS = 2_000;
export const LIST_POLL_MS = 5_000;

/** The original can be downloaded. A picture only once the server re-encoded it from its
 * pixels (no GPS or other metadata): until then the server refuses it (409), even when its
 * reading failed (the upload is kept to be read again, never served). */
export function canDownloadOriginal(document: Pick<DocumentSummary, "original_downloadable">): boolean {
  return document.original_downloadable;
}

/** refetchInterval of a document detail: poll while it is being read. */
export function detailPollInterval(status: DocumentStatus | undefined): number | false {
  return isInProgress(status) ? DETAIL_POLL_MS : false;
}

/** refetchInterval of a list: poll while one of the loaded documents is being read. */
export function listPollInterval(documents: Pick<DocumentSummary, "status">[] | undefined): number | false {
  return documents?.some((d) => isInProgress(d.status)) ? LIST_POLL_MS : false;
}

/** Reasons set by the server on a failed document, or on a pending one waiting for a retry. */
const ERROR_EXPLANATIONS: Record<string, string> = {
  unreadable: "Le document est illisible : aucun texte n'a pu en être tiré.",
  too_many_pages: "Le document compte trop de pages pour être lu.",
  encrypted: "PDF protégé par mot de passe : retirez la protection, puis envoyez-le à nouveau.",
  too_slow: "La lecture a pris trop de temps et a été interrompue.",
  image_too_large: "L'image est trop grande (en pixels) pour être lue.",
  unsupported_type: "Ce type de fichier ne peut pas être lu.",
  too_large: "Le fichier est trop volumineux pour être lu.",
  original_purged: "L'original n'est plus conservé : le document ne peut plus être lu.",
  original_unreadable: "L'original chiffré n'a pas pu être déchiffré.",
  processing_error: "Erreur inattendue pendant la lecture.",
  extraction_crashed:
    "La lecture a échoué plusieurs fois de suite (fichier endommagé, ou serveur à court de mémoire) : vous pouvez la relancer.",
  extraction_unavailable: "Lecture locale indisponible pour le moment : nouvel essai automatique dans quelques minutes.",
  documents_unavailable:
    "Le stockage des documents est indisponible sur le serveur : nouvel essai automatique dans quelques minutes.",
  ocr_unavailable:
    "La reconnaissance de texte (OCR) est indisponible pour le moment : nouvel essai automatique dans quelques minutes.",
};

/** French explanation of an error code (unknown codes are named, never hidden). */
export function errorExplanation(code: string | null): string | null {
  if (!code) return null;
  return ERROR_EXPLANATIONS[code] ?? `La lecture a échoué (code « ${code} »).`;
}

export interface StatusInfo {
  tone: "info" | "warning" | "error";
  text: string;
}

/** What to tell the owner about the processing state; null once the document is ready. */
export function statusInfo(document: Pick<DocumentSummary, "status" | "error">): StatusInfo | null {
  switch (document.status) {
    case "pending":
      return document.error
        ? {
            tone: "warning",
            text: ERROR_EXPLANATIONS[document.error] ?? "Lecture reportée : nouvel essai automatique dans quelques minutes.",
          }
        : { tone: "info", text: "En attente de lecture : le document sera lu dans quelques instants." };
    case "processing":
      return { tone: "info", text: "Lecture en cours sur le serveur…" };
    case "failed":
      return { tone: "error", text: errorExplanation(document.error) ?? "La lecture a échoué." };
    case "rejected":
      return {
        tone: "error",
        text: document.error ? `Document refusé. ${errorExplanation(document.error)}` : "Document refusé.",
      };
    default:
      return null;
  }
}

// --- Extracted text --------------------------------------------------------------------

/** Below this OCR confidence, the owner is asked to check the text. */
export const LOW_CONFIDENCE = 0.6;
export const TEXT_MAX = 200_000;

export function formatConfidence(confidence: number | null): string {
  if (confidence === null || !Number.isFinite(confidence)) return "";
  return `${Math.round(Math.min(1, Math.max(0, confidence)) * 100)}${NBSP}%`;
}

export function isLowConfidence(confidence: number | null): boolean {
  return confidence !== null && Number.isFinite(confidence) && confidence < LOW_CONFIDENCE;
}

const SOURCE_LABELS: Record<string, string> = {
  ocr: "Lu par OCR",
  pdf: "Texte du PDF",
  text: "Fichier texte",
  speech: "Dictée transcrite",
};

/** "Lu par OCR, confiance 84 %", "Texte du PDF"... null before any reading. */
export function textSourceLabel(source: string | null, confidence: number | null): string | null {
  if (!source) return null;
  const label = SOURCE_LABELS[source] ?? source;
  const percent = formatConfidence(confidence);
  return percent ? `${label}, confiance ${percent}` : label;
}

/** The text can be corrected once the server is done reading (even after a failure). */
export function canEditText(status: DocumentStatus): boolean {
  return !isInProgress(status);
}

export function validateText(text: string): string | null {
  return text.length > TEXT_MAX ? "Le texte est trop long (200 000 caractères au plus)." : null;
}

// --- Title -----------------------------------------------------------------------------

export const TITLE_MAX = 200;

/** Normalised new title (spaces collapsed, like the API); null when unchanged. */
export function parseTitle(raw: string, current: string): Parsed<string | null> {
  const title = raw.split(/\s+/).filter(Boolean).join(" ");
  if (!title) return { ok: false, error: "Le titre ne peut pas être vide." };
  if (title.length > TITLE_MAX) {
    return { ok: false, error: `Le titre ne peut pas dépasser ${TITLE_MAX} caractères.` };
  }
  return { ok: true, value: title === current ? null : title };
}

// --- Sizes and dates -------------------------------------------------------------------

const SIZE_UNITS = ["o", "Ko", "Mo", "Go", "To"];

/** Bytes -> French human-readable size ("1,2 Mo"); 1 Ko = 1 024 octets. */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  if (unit === 0) return `${bytes}${NBSP}o`;
  let digits = value < 100 ? 1 : 0;
  let rounded = Number(value.toFixed(digits));
  // 1023.96 Ko would read "1 024 Ko": move to the next unit instead.
  if (rounded >= 1024 && unit < SIZE_UNITS.length - 1) {
    unit += 1;
    rounded = Number((value / 1024).toFixed(1));
    digits = 1;
  }
  const text = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: digits }).format(rounded);
  return `${text}${NBSP}${SIZE_UNITS[unit]}`;
}

/** "Original conservé jusqu'au 10 janv. 2027", "… sans limite" or "Original supprimé". */
export function originalLabel(document: Pick<DocumentSummary, "original_available" | "original_until">): string {
  if (!document.original_available) return "Original supprimé";
  if (!document.original_until) return "Original conservé sans limite";
  return `Original conservé jusqu'au ${formatDate(document.original_until)}`;
}

/** Retention explained after an upload or next to the "keep" switch. */
export function retentionHint(document: Pick<DocumentSummary, "original_available" | "original_until">): string {
  if (!document.original_available) {
    return "L'original n'est plus conservé ; le texte extrait reste disponible.";
  }
  if (!document.original_until) {
    return "L'original chiffré est conservé jusqu'à ce que vous supprimiez le document.";
  }
  return `L'original chiffré sera supprimé le ${formatDate(document.original_until)} ; le texte extrait reste disponible.`;
}

// --- Money -----------------------------------------------------------------------------

const CURRENCY_SYMBOLS: Record<string, string> = { EUR: "€", USD: "$", GBP: "£" };
const DECIMAL_STRING = /^([+-]?)(\d+)(?:\.(\d*))?$/;

/**
 * API decimal string -> French amount ("1234.5" -> "1 234,50 €"). String and BigInt
 * arithmetic only: no float rounding. More than 2 decimals round half away from zero.
 * An unparseable value is returned as is.
 */
export function formatMoney(value: Decimal | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined) return "";
  const match = DECIMAL_STRING.exec(value.trim());
  if (!match) return value;
  const [, sign, integer = "0", fraction = ""] = match;
  let cents = BigInt(integer + fraction.padEnd(2, "0").slice(0, 2));
  if (fraction.length > 2 && Number(fraction[2]) >= 5) cents += 1n;
  const units = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, NNBSP);
  const rest = (cents % 100n).toString().padStart(2, "0");
  const minus = sign === "-" && cents !== 0n ? "-" : "";
  const symbol = CURRENCY_SYMBOLS[currency] ?? currency;
  return `${minus}${units},${rest}${NBSP}${symbol}`;
}

// --- Analysis --------------------------------------------------------------------------

export interface SensitivityDetail {
  category: SensitivityCategory;
  label: string;
  /** Why it was flagged (e.g. "IBAN"), never the detected value. */
  reasons: string[];
}

export function sensitivityDetails(
  document: Pick<DocumentSummary, "sensitivity" | "analysis">,
): SensitivityDetail[] {
  const reasons = document.analysis?.sensitivity_reasons ?? {};
  return document.sensitivity.map((category) => {
    const listed = reasons[category];
    return {
      category,
      label: SENSITIVITY_LABELS[category] ?? category,
      reasons: Array.isArray(listed) ? listed.filter((r): r is string => typeof r === "string") : [],
    };
  });
}

export interface ReceiptReading {
  merchant: string | null;
  txDate: string | null;
  total: Decimal | null;
  currency: string;
  confidence: number | null;
  cardPayment: boolean;
  warnings: string[];
  /** The line of the receipt each value was read from. */
  lines: { merchant: string | null; txDate: string | null; total: string | null };
}

const str = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value : null);

/** The receipt read locally (`analysis.receipt`), parsed defensively: it is stored JSON. */
export function readReceipt(analysis: DocumentAnalysis | null): ReceiptReading | null {
  const receipt: unknown = analysis?.receipt;
  if (!receipt || typeof receipt !== "object") return null;
  const raw = receipt as Record<string, unknown>;
  const lines = raw.lines && typeof raw.lines === "object" ? (raw.lines as Record<string, unknown>) : {};
  const currency = str(raw.currency);
  const confidence = typeof raw.confidence === "number" && Number.isFinite(raw.confidence) ? raw.confidence : null;
  const total = str(raw.total);
  return {
    merchant: str(raw.merchant),
    txDate: str(raw.tx_date),
    total: total && DECIMAL_STRING.test(total) ? total : null,
    currency: currency && /^[A-Z]{3}$/.test(currency) ? currency : "EUR",
    confidence,
    cardPayment: raw.card_payment === true,
    warnings: Array.isArray(raw.warnings) ? raw.warnings.filter((w): w is string => typeof w === "string") : [],
    lines: { merchant: str(lines.merchant), txDate: str(lines.tx_date), total: str(lines.total) },
  };
}

/** Where the Finances page picks the receipt up to propose a transaction. */
export function financesReceiptHref(documentId: string): string {
  return `/finances?receipt=${encodeURIComponent(documentId)}`;
}

/** Link to the object a document comes from (provenance). */
export function sourceLink(type: DocumentSourceType | null, id: string | null): { label: string; href: string } | null {
  if (!type || !id) return null;
  const label = SOURCE_TYPE_LABELS[type] ?? type;
  const pages: Record<DocumentSourceType, string> = {
    goal: `/goals?id=${encodeURIComponent(id)}`,
    task: "/tasks",
    journal_entry: "/journal",
    conversation: "/chat",
    message: "/chat",
    transaction: "/finances",
    mail_message: `/mail?id=${encodeURIComponent(id)}`,
  };
  return { label, href: pages[type] ?? "/" };
}

// --- Upload ----------------------------------------------------------------------------

/** What the server accepts (checked by signature server-side, never by name). */
export const ACCEPTED_FILES =
  ".pdf,.jpg,.jpeg,.png,.webp,.heic,.heif,.txt,application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,text/plain";

/** Default retention of the encrypted original on the server (setting). */
export const DEFAULT_RETENTION_DAYS = 90;

export type UploadPurpose = "document" | "receipt";

export const UPLOAD_PURPOSES: { value: UploadPurpose; label: string }[] = [
  { value: "document", label: "Document" },
  { value: "receipt", label: "Ticket de caisse" },
];

export interface UploadValues {
  file: File | null;
  spaceId: string;
  purpose: UploadPurpose;
  title: string;
  keepOriginal: boolean;
}

/** Multipart body of `POST /v1/documents`, or the reason it cannot be sent. */
export function buildUploadForm(values: UploadValues): Parsed<FormData> {
  if (!values.file) return { ok: false, error: "Choisissez un fichier à envoyer." };
  if (values.file.size === 0) return { ok: false, error: "Le fichier est vide." };
  if (!values.spaceId) return { ok: false, error: "Choisissez un espace." };
  const title = values.title.split(/\s+/).filter(Boolean).join(" ");
  if (title.length > TITLE_MAX) {
    return { ok: false, error: `Le titre ne peut pas dépasser ${TITLE_MAX} caractères.` };
  }
  const form = new FormData();
  form.set("space_id", values.spaceId);
  form.set("purpose", values.purpose);
  if (title) form.set("title", title);
  form.set("keep_original", values.keepOriginal ? "true" : "false");
  form.set("file", values.file, values.file.name);
  return { ok: true, value: form };
}

/** Upload errors, with the server's size limit when it gives one. */
export function uploadErrorMessage(error: unknown): string {
  if (isApiError(error, "document_too_large")) {
    const max = error.problem?.max_bytes;
    if (typeof max === "number" && max > 0) {
      return `Le fichier est trop volumineux (${formatFileSize(max)} au maximum).`;
    }
  }
  return errorMessage(error);
}
