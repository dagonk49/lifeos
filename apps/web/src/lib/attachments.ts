// Pure helpers for chat attachments (F01): which attached documents can go with a message.
// The server enforces the same rules; these only explain them before sending.

import type { DocumentSummary, SensitivityCategory } from "./types";

export const MAX_ATTACHMENTS = 3;

export type AttachmentState =
  | "reading"
  | "postponed"
  | "ready"
  | "sensitive"
  | "local"
  | "unreadable"
  | "unavailable";

/**
 * State of an attached document. `gone`: it could not be loaded (deleted meanwhile, or
 * never loaded). Only "reading" holds the message back; every state but "ready" means the
 * document is left out of the message.
 */
export function attachmentState(
  document: (Pick<DocumentSummary, "status" | "sensitivity" | "error"> & { local_only?: boolean }) | undefined,
  gone = false,
): AttachmentState {
  if (gone) return "unavailable";
  if (!document) return "reading";
  if (document.status === "pending" || document.status === "processing") {
    // Pending with an error code: the server will try again later (e.g. reader down).
    return document.error ? "postponed" : "reading";
  }
  if (document.status !== "ready") return "unreadable";
  if (document.sensitivity.length > 0) return "sensitive";
  // « Toujours garder en local »: the owner's choice.
  return document.local_only ? "local" : "ready";
}

/** Polling of an attached document, in ms (false: no more polling). */
export function attachmentPollInterval(state: AttachmentState): number | false {
  if (state === "reading") return 1500;
  if (state === "postponed") return 10_000;
  return false;
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
    case "postponed":
      return "Lecture reportée (nouvel essai automatique) : non jointe à ce message";
    case "ready":
      return "Texte prêt";
    case "unreadable":
      return "Illisible : ne sera pas envoyé";
    case "unavailable":
      return "Document introuvable : ne sera pas envoyé";
    case "sensitive": {
      const kinds = sensitivity.map((s) => SENSITIVITY_LABELS[s] ?? s).join(", ");
      return `Document sensible${kinds ? ` (${kinds})` : ""} : jamais envoyé à l'IA`;
    }
    case "local":
      return "Gardé en local (votre choix) : jamais envoyé à l'IA";
  }
}

/** Ids that go with the message: ready and not kept local, in the order attached. */
export function sendableAttachments(ids: readonly string[], states: readonly AttachmentState[]): string[] {
  return ids.filter((_, index) => states[index] === "ready");
}

/** Whether the message must wait: a document is still being read. */
export function attachmentsReading(states: readonly AttachmentState[]): boolean {
  return states.some((state) => state === "reading");
}
