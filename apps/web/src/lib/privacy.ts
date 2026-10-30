// Pure helpers for the control shown before a message goes to the assistant IA: which
// attached documents stay on the server, and which identifiers are masked (by kind, never
// the values). The server decides (POST /v1/chat/preview, same rules as the sending);
// these only put it into words.

import { SENSITIVITY_LABELS } from "./documents";
import type { AttachmentState } from "./attachments";
import type {
  AttachmentRefusal,
  DocumentSummary,
  MaskCounts,
  MaskKind,
  SendPreview,
  SensitivityCategory,
} from "./types";

/** Singular and plural label of each kind of identifier, the most sensitive first. */
const MASK_LABELS: Record<MaskKind, readonly [string, string]> = {
  iban: ["IBAN", "IBAN"],
  card: ["numéro de carte bancaire", "numéros de carte bancaire"],
  nir: ["numéro de sécurité sociale", "numéros de sécurité sociale"],
  passport: ["numéro de passeport", "numéros de passeport"],
  id_number: ["numéro de pièce d'identité", "numéros de pièce d'identité"],
  tax_number: ["numéro fiscal", "numéros fiscaux"],
  secret: ["mot de passe ou code secret", "mots de passe ou codes secrets"],
  email: ["adresse e-mail", "adresses e-mail"],
  phone: ["numéro de téléphone", "numéros de téléphone"],
};
const MASK_ORDER = Object.keys(MASK_LABELS) as MaskKind[];

export function maskedTotal(counts: MaskCounts): number {
  return MASK_ORDER.reduce((total, kind) => total + (counts[kind] ?? 0), 0);
}

/** « 1 IBAN, 2 numéros de téléphone » (empty when nothing is masked). */
export function maskedSummary(counts: MaskCounts): string {
  return MASK_ORDER.filter((kind) => (counts[kind] ?? 0) > 0)
    .map((kind) => {
      const count = counts[kind] ?? 0;
      return `${count} ${MASK_LABELS[kind][count > 1 ? 1 : 0]}`;
    })
    .join(", ");
}

function sensitivityText(sensitivity: readonly SensitivityCategory[]): string {
  const kinds = sensitivity.map((s) => (SENSITIVITY_LABELS[s] ?? s).toLowerCase()).join(", ");
  return `document sensible${kinds ? ` (${kinds})` : ""}`;
}

/** Why a document stays on the server, as decided by the server. */
export function refusalText(reason: AttachmentRefusal, sensitivity: readonly SensitivityCategory[] = []): string {
  switch (reason) {
    case "sensitive":
      return sensitivityText(sensitivity);
    case "local_only":
      return "gardé en local à votre demande";
    case "not_ready":
      return "lecture pas encore terminée";
    case "unreadable":
      return "aucun texte lisible";
    case "not_found":
      return "document introuvable";
    case "not_allowed":
      return "cet agent n'a pas accès aux documents de cet espace";
  }
}

/** Why a document is left out, as already known by the page (it was not even checked). */
function stateText(state: AttachmentState, sensitivity: readonly SensitivityCategory[]): string {
  switch (state) {
    case "sensitive":
      return sensitivityText(sensitivity);
    case "local":
      return refusalText("local_only");
    case "postponed":
    case "reading":
      return refusalText("not_ready");
    case "unreadable":
      return refusalText("unreadable");
    case "unavailable":
      return refusalText("not_found");
    case "ready":
      return "";
  }
}

export interface ReviewItem {
  id: string;
  title: string;
  /** True: its text goes with the message (identifiers masked). */
  sent: boolean;
  /** Left out: why. Sent: what is masked in it (may be empty). */
  detail: string;
}

export interface SendReview {
  /** Identifiers masked in the owner's message. */
  masked: MaskCounts;
  items: ReviewItem[];
  /** Documents that go with the message, in order. */
  attachments: string[];
  /** Something is masked or left out: the owner confirms before sending. */
  needed: boolean;
}

/**
 * The control before sending, from the attachments shown (ids, page states, documents)
 * and the server's preview of those the page would send.
 */
export function buildReview(
  ids: readonly string[],
  states: readonly AttachmentState[],
  documents: readonly (Pick<DocumentSummary, "title" | "sensitivity"> | undefined)[],
  preview: SendPreview,
): SendReview {
  const checked = new Map(preview.attachments.map((item) => [item.document_id, item]));
  const items: ReviewItem[] = ids.map((id, index) => {
    const document = documents[index];
    const title = document?.title ?? "Document";
    const state = states[index] ?? "reading";
    const decision = state === "ready" ? checked.get(id) : undefined;
    if (state !== "ready") {
      return { id, title, sent: false, detail: stateText(state, document?.sensitivity ?? []) };
    }
    if (!decision) return { id, title, sent: false, detail: refusalText("not_found") };
    if (!decision.sent) {
      return { id, title, sent: false, detail: refusalText(decision.reason ?? "not_found", decision.sensitivity) };
    }
    return { id, title, sent: true, detail: maskedSummary(decision.masked) };
  });
  return withNeed({ masked: preview.masked, items, attachments: items.filter((i) => i.sent).map((i) => i.id) });
}

function withNeed(review: Omit<SendReview, "needed">): SendReview {
  const needed =
    maskedTotal(review.masked) > 0 || review.items.some((item) => !item.sent || item.detail !== "");
  return { ...review, needed };
}

/** The review once the owner removed one attachment from the message. */
export function withoutAttachment(review: SendReview, id: string): SendReview {
  const items = review.items.filter((item) => item.id !== id);
  return withNeed({
    masked: review.masked,
    items,
    attachments: review.attachments.filter((other) => other !== id),
  });
}
