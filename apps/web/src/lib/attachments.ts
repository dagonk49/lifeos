// Pure helpers for chat attachments (F01): which attached documents can go with a message.
// The server enforces the same rules; these only explain them before sending.

import type { DocumentSummary, SensitivityCategory } from "./types";

export const MAX_ATTACHMENTS = 3;

export type AttachmentState = "reading" | "ready" | "sensitive" | "unreadable";

export function attachmentState(
  document: Pick<DocumentSummary, "status" | "sensitivity"> | undefined,
): AttachmentState {
  if (!document || document.status === "pending" || document.status === "processing") return "reading";
  if (document.status !== "ready") return "unreadable";
  return document.sensitivity.length > 0 ? "sensitive" : "ready";
}

const SENSITIVITY_LABELS: Record<SensitivityCategory, string> = {
  identity: "identité",
  health: "santé",
  bank: "banque",
};

export function attachmentStateLabel(
  state: AttachmentState,
  sensitivity: readonly SensitivityCategory[] = [],
): string {
  switch (state) {
    case "reading":
      return "Lecture locale en cours…";
    case "ready":
      return "Texte prêt";
    case "unreadable":
      return "Illisible : ne sera pas envoyé";
    case "sensitive": {
      const kinds = sensitivity.map((s) => SENSITIVITY_LABELS[s] ?? s).join(", ");
      return `Document sensible${kinds ? ` (${kinds})` : ""} : jamais envoyé à l'IA`;
    }
  }
}

/** Ids that will go with the message: ready and not sensitive. */
export function sendableAttachments(documents: readonly (DocumentSummary | undefined)[]): string[] {
  return documents.filter((d): d is DocumentSummary => attachmentState(d) === "ready").map((d) => d.id);
}

export function attachmentsReading(documents: readonly (DocumentSummary | undefined)[]): boolean {
  return documents.some((d) => attachmentState(d) === "reading");
}
