import type { MailSummary } from "@/lib/types";

export const MAIL_KEY = "mail";

/** Who sent it, as written by the sender (a display name proves nothing). */
export function senderLabel(mail: Pick<MailSummary, "from_name" | "from_address">): string {
  if (mail.from_name && mail.from_address) return `${mail.from_name} <${mail.from_address}>`;
  return mail.from_address ?? mail.from_name ?? "Expéditeur inconnu";
}

export function subjectLabel(subject: string): string {
  return subject.trim() || "(sans objet)";
}

/** A file size in French units. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} Mo`;
}

/** Active content found in a PDF from a third party (ADR 0011). */
export const ACTIVE_CONTENT_LABELS: Record<string, string> = {
  javascript: "JavaScript",
  open_action: "action à l'ouverture",
  automatic_action: "actions automatiques",
  launch: "lancement d'un programme",
  embedded_file: "fichier intégré",
  rich_media: "média intégré",
  xfa_form: "formulaire XFA",
};

export function activeContentList(codes: string[] | undefined): string | null {
  if (!codes || codes.length === 0) return null;
  return codes.map((code) => ACTIVE_CONTENT_LABELS[code] ?? code).join(", ");
}

const LABEL_ERRORS: Record<string, string> = {
  consent_missing: "Lecture des e-mails non autorisée dans cet espace : synchronisation arrêtée.",
  scope_missing: "Lecture des e-mails non accordée chez le fournisseur.",
  account_inactive: "Compte à reconnecter : synchronisation arrêtée.",
  label_gone: "Libellé introuvable chez le fournisseur (supprimé ou renommé).",
  rate_limited: "Trop de requêtes chez le fournisseur : nouvel essai automatique.",
  too_many_pages: "Libellé très chargé : synchronisation en plusieurs fois.",
  provider_unavailable: "Fournisseur non configuré sur ce serveur.",
};

/** Why the last sync of a followed label failed, in French. */
export function labelErrorMessage(code: string | null): string | null {
  if (!code) return null;
  return LABEL_ERRORS[code] ?? "Fournisseur injoignable : nouvel essai automatique.";
}

// --- Writing and sending (task 18) ------------------------------------------------------

const ADDRESS = /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$/;

/** Addresses typed in one field, separated by commas, semicolons or spaces (lower case). */
export function parseAddresses(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[\s,;]+/)) {
    const address = part.trim().toLowerCase();
    if (address) seen.add(address);
  }
  return [...seen];
}

export function isValidAddress(address: string): boolean {
  return ADDRESS.test(address.trim().toLowerCase());
}

export interface DraftFields {
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
}

export type DraftErrors = Partial<Record<keyof DraftFields, string>>;

/** Field errors of the compose form, in French (same rules as the server). */
export function draftErrors(fields: DraftFields, { sending }: { sending: boolean }): DraftErrors {
  const errors: DraftErrors = {};
  for (const key of ["to", "cc", "bcc"] as const) {
    const invalid = parseAddresses(fields[key]).filter((a) => !isValidAddress(a));
    if (invalid.length > 0) errors[key] = `Adresse non valide : ${invalid.join(", ")}`;
  }
  const total = new Set([...parseAddresses(fields.to), ...parseAddresses(fields.cc), ...parseAddresses(fields.bcc)])
    .size;
  if (total > 50) errors.to = "50 destinataires au plus.";
  if (/[\r\n]/.test(fields.subject)) errors.subject = "L'objet doit tenir sur une ligne.";
  else if (fields.subject.trim().length > 300) errors.subject = "300 caractères au plus.";
  if (sending) {
    if (parseAddresses(fields.to).length === 0 && !errors.to) errors.to = "Indiquez au moins un destinataire.";
    if (!fields.subject.trim() && !fields.body.trim()) errors.body = "Écrivez un objet ou un message.";
  }
  return errors;
}

export const DRAFT_STATE_LABELS: Record<string, string> = {
  draft: "Brouillon",
  awaiting_approval: "En attente de votre approbation",
  sending: "Envoi en cours",
  unknown: "État incertain — vérifiez vos e-mails envoyés",
  failed: "Non envoyé",
  sent: "Envoyé",
};

/** « Re: » once, as mail clients do. */
export function replySubject(subject: string): string {
  const trimmed = subject.trim();
  return /^re\s*:/i.test(trimmed) ? trimmed : `Re: ${trimmed}`;
}
