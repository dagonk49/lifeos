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
