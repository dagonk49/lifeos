import type { Problem } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly problem: Problem | null;

  constructor(status: number, problem: Problem | null) {
    super(problem?.detail ?? problem?.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem?.code ?? "http_error";
    this.problem = problem;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  /** Adds an Idempotency-Key so that a retried creation is not duplicated. */
  idempotent?: boolean;
  signal?: AbortSignal;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotent) headers["Idempotency-Key"] = newIdempotencyKey();
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    credentials: "same-origin",
    signal: options.signal,
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, (data as Problem | null) ?? null);
  return data as T;
}

/**
 * Multipart upload (documents, dictations). The browser sets the Content-Type with its
 * boundary; the server checks the type by signature, never by name or declared type.
 */
export async function upload<T>(
  path: string,
  form: FormData,
  options: { idempotent?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.idempotent) headers["Idempotency-Key"] = newIdempotencyKey();
  const response = await fetch(path, {
    method: "POST",
    headers,
    body: form,
    credentials: "same-origin",
    signal: options.signal,
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, (data as Problem | null) ?? null);
  return data as T;
}

/** The file name of an `attachment` Content-Disposition (RFC 6266, `filename*` first). */
export function attachmentName(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const extended = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (extended?.[1]) {
    try {
      return decodeURIComponent(extended[1]);
    } catch {
      // Malformed encoding: fall back on the plain name.
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain?.[1] ?? fallback;
}

/**
 * Downloads a file through fetch (so that a problem, e.g. a recent passkey sign-in to
 * confirm, comes back as an ApiError usable with `withReauth`), then hands it to the
 * browser as a download. The file is held in memory meanwhile: for exports only.
 */
export async function download(path: string, fallbackName: string): Promise<string> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    throw new ApiError(response.status, (data as Problem | null) ?? null);
  }
  const name = attachmentName(response.headers.get("Content-Disposition"), fallbackName);
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.rel = "noopener";
    document.body.append(link);
    link.click();
    link.remove();
  } finally {
    // After the click has started the download.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return name;
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code);
}

/**
 * Runs `operation`; if the server asks for a recent authentication ("validation
 * renforcée"), runs `reauthenticate` (a passkey ceremony) and retries once.
 */
export async function withReauth<T>(
  operation: () => Promise<T>,
  reauthenticate: () => Promise<unknown>,
): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (!isApiError(error, "reauthentication_required")) throw error;
    await reauthenticate();
    return operation();
  }
}

const MESSAGES: Record<string, string> = {
  unauthenticated: "Session expirée : reconnectez-vous.",
  cloud_consent_required:
    "L'assistant IA n'est pas activé pour cet espace. Activez-le dans les réglages.",
  llm_unavailable: "L'assistant IA est indisponible pour le moment. Vos données restent accessibles.",
  llm_budget_exceeded: "Le budget de l'assistant IA est atteint.",
  llm_daily_quota_reached:
    "Le quota gratuit du jour est atteint. Il se renouvelle chaque jour vers 9 h (heure de Paris).",
  llm_quota_exceeded:
    "Le quota du fournisseur IA est épuisé pour le moment. Rien n'est facturé ; réessayez plus tard.",
  llm_billing_exhausted: "Le crédit prépayé de l'assistant IA est épuisé.",
  llm_paused: "L'assistant IA est en pause (arrêt d'urgence). Réactivez-le dans les réglages.",
  cloud_call_denied: "Cet appel à l'IA n'est pas autorisé.",
  agent_permission_required:
    "Cet agent n'a pas encore accès aux données nécessaires dans cet espace.",
  nothing_to_consolidate: "Aucun nouveau message à consolider dans cette conversation.",
  review_in_future: "Cette période n'a pas encore commencé.",
  goal_target_required: "Un objectif mesurable demande une valeur cible.",
  task_not_found: "Cette tâche est introuvable ou a été supprimée.",
  goal_not_found: "Cet objectif est introuvable ou a été supprimé.",
  milestone_not_found: "Ce jalon n'existe plus.",
  goal_space_mismatch: "La tâche et l'objectif doivent appartenir au même espace.",
  task_blocked:
    "Cette tâche attend d'autres tâches encore ouvertes : terminez-les ou annulez-les d'abord.",
  dependency_cycle:
    "Ce lien créerait une boucle : une tâche finirait par s'attendre elle-même. Rien n'a été modifié.",
  dependency_space_mismatch: "Une tâche ne peut attendre qu'une tâche du même espace.",
  dependency_not_found: "Ce lien entre tâches n'existe plus : rechargez la page.",
  too_many_dependencies: "Une tâche peut attendre au plus 20 autres tâches.",
  goal_start_required: "Un objectif mesurable demande une valeur de départ.",
  goal_dates_invalid: "L'échéance doit être postérieure à la date de début.",
  fact_not_active: "Ce fait n'est plus actif.",
  invalid_pair: "Choisissez deux éléments distincts du même espace.",
  llm_blocked: "Le fournisseur IA a refusé de répondre à cette demande.",
  llm_invalid_output: "La réponse de l'IA n'a pas pu être validée. Réessayez.",
  approval_required: "Cette action demande votre approbation explicite.",
  approval_not_pending: "Cette proposition a déjà été traitée (ou n'est plus valable) : rien n'a été refait.",
  approval_params_mismatch: "Ces paramètres ne sont pas ceux que vous avez approuvés : rien n'a été fait.",
  approval_not_editable: "Cette proposition ne peut pas être modifiée : refaites la demande.",
  // Calendar writes (task 16)
  event_changed:
    "L'événement a changé dans l'agenda depuis l'aperçu : rien n'a été modifié. Refaites la demande.",
  event_gone: "Cet événement n'existe plus dans l'agenda.",
  target_gone: "L'élément visé n'existe plus chez le fournisseur.",
  event_not_found: "Cet événement est introuvable : actualisez l'agenda.",
  event_not_organizer:
    "Seul l'organisateur peut déplacer cet événement. Vous pouvez l'annuler dans votre agenda.",
  event_in_past: "Ce moment est déjà passé : choisissez une date à venir.",
  event_unchanged: "L'événement a déjà lieu à ce moment-là.",
  invalid_event_times: "Indiquez un début et une fin, la fin après le début (14 jours au plus).",
  invalid_guest: "Une adresse de participant n'est pas valide.",
  calendar_write_unavailable:
    "Aucun agenda de cet espace ne peut être modifié : autorisez « Modifier l'agenda » dans Réglages > Comptes connectés.",
  calendar_read_only: "Cet agenda est en lecture seule.",
  calendar_gone: "Cet agenda n'est plus suivi ou n'existe plus.",
  connector_unavailable: "Le fournisseur ne répond pas pour le moment. Réessayez plus tard.",
  outcome_unknown:
    "Le fournisseur n'a pas confirmé la modification. LifeOS vérifie dans quelques minutes, sans rien renvoyer.",
  not_applied: "La modification n'a pas eu lieu chez le fournisseur : vous pouvez refaire la demande.",
  too_many_attempts: "Cette modification a été tentée trop de fois : refaites la demande.",
  superseded: "Remplacée par une proposition plus récente.",
  reauthentication_required: "Confirmez votre identité avec votre passkey.",
  origin_mismatch:
    "Requête refusée : elle ne provient pas de l'adresse de LifeOS. Rechargez la page ; si l'erreur persiste, vérifiez LIFEOS_PUBLIC_ORIGIN.",
  version_conflict: "Cet élément a été modifié ailleurs : rechargez la page.",
  memory_protected: "Ce souvenir est protégé : confirmez la suppression.",
  last_passkey: "Impossible de révoquer votre dernière passkey.",
  passkey_verification_failed: "La vérification de la passkey a échoué.",
  enrollment_token_invalid: "Lien d'enrôlement invalide ou expiré.",
  validation_error: "Certaines valeurs saisies sont invalides.",
  // Documents, finances, voice (step 4)
  document_too_large: "Le fichier est trop volumineux.",
  document_empty: "Le fichier est vide.",
  unsupported_type:
    "Type de fichier refusé. Formats acceptés : PDF, images (JPEG, PNG, WebP, HEIC) et texte.",
  unsupported_media_type: "Envoi invalide : réessayez.",
  documents_unavailable:
    "Le stockage des documents n'est pas configuré sur le serveur (clé de chiffrement absente).",
  document_not_found: "Ce document est introuvable ou a été supprimé.",
  document_processing: "Le document est encore en cours de lecture.",
  original_not_sanitized:
    "Cette image n'a pas encore été nettoyée de ses métadonnées (position GPS…) : relancez sa lecture pour pouvoir la télécharger.",
  image_reading_disabled:
    "La lecture des images n'est pas activée sur ce serveur : envoyez un PDF ou un texte, ou activez le service de lecture locale.",
  original_purged: "L'original n'est plus conservé (durée de conservation écoulée).",
  document_title_required: "Le titre ne peut pas être vide.",
  document_not_reprocessable: "Ce document ne peut pas être relu.",
  source_not_found: "L'élément d'origine est introuvable.",
  source_space_mismatch: "Le document et son origine doivent appartenir au même espace.",
  receipt_not_ready: "Le ticket est encore en cours de lecture.",
  not_a_receipt: "Ce document n'est pas un ticket de caisse.",
  category_not_found: "Cette catégorie n'existe plus.",
  category_exists: "Une catégorie porte déjà ce nom dans cet espace.",
  category_archived: "Cette catégorie est archivée.",
  category_space_mismatch: "La catégorie appartient à un autre espace.",
  document_space_mismatch: "Le ticket appartient à un autre espace.",
  receipt_document_required: "Une opération issue d'un ticket doit indiquer ce ticket.",
  tx_date_in_future: "La date de l'opération ne peut pas être dans le futur.",
  invalid_month: "Mois invalide.",
  transaction_not_found: "Cette opération est introuvable ou a été supprimée.",
  transcription_unavailable: "La dictée n'est pas activée sur ce serveur.",
  dictation_not_found: "Cette dictée a expiré ou a été supprimée.",
  // Connected accounts (step 5, ADR 0012)
  connectors_unavailable:
    "Les comptes connectés ne sont pas configurés sur ce serveur (voir le guide d'installation).",
  invalid_capabilities: "Choisissez au moins un usage à autoriser.",
  oauth_state_invalid:
    "Cette demande de connexion a expiré ou a déjà servi : recommencez depuis les réglages.",
  oauth_code_invalid:
    "Le fournisseur a refusé la connexion (code expiré ou déjà utilisé) : recommencez depuis les réglages.",
  connector_consent_denied: "L'accès n'a pas été accordé chez le fournisseur : aucun compte n'a été connecté.",
  connector_provider_error: "Le fournisseur du compte ne répond pas correctement. Réessayez plus tard.",
  connector_not_found: "Ce compte connecté est introuvable.",
  connector_grant_not_found: "Cette autorisation a déjà été retirée.",
  connector_reauth_required: "Ce compte doit être reconnecté (Réglages > Comptes connectés).",
  connector_disconnected: "Ce compte est déconnecté : connectez-le à nouveau pour l'utiliser.",
  connector_scope_missing:
    "Ces accès n'ont pas été accordés chez le fournisseur : cochez-les de nouveau pour les accepter sur sa page.",
  connector_account_mismatch:
    "Un autre compte a été choisi chez le fournisseur : rien n'a été modifié et l'accès reçu a été retiré. Recommencez en choisissant le même compte.",
  invalid_reconnect: "Ce compte connecté est introuvable : actualisez la page.",
  connector_capability_missing: "Ce compte n'est pas autorisé pour cet usage dans cet espace.",
  attachment_sensitive:
    "Ce document reste sur votre serveur (sensible ou gardé en local) : son contenu n'est jamais envoyé à l'assistant IA. Retirez-le du message.",
  attachment_not_ready: "La pièce jointe est encore en cours de lecture.",
  attachment_unreadable: "La pièce jointe n'a pas pu être lue : aucun texte à transmettre.",
  attachment_not_allowed:
    "Cet agent n'a pas accès aux documents de cet espace : retirez la pièce jointe ou accordez-lui l'accès.",
  space_not_found: "Cet espace est introuvable.",
  // Notifications (F09)
  notification_preferences_invalid:
    "Ces réglages de notifications sont invalides : vérifiez les créneaux, les heures de regroupement et le maximum par jour.",
  notification_not_found: "Cette notification n'existe plus.",
  // Home page (task 23)
  dashboard_layout_invalid:
    "Cette disposition de l'accueil est invalide (carte inconnue ou en double) : rechargez la page puis recommencez.",
  // E-mails (task 17)
  mail_not_found: "Cet e-mail est introuvable dans cet espace (supprimé, ou libellé plus suivi).",
  mail_sensitive:
    "Cet e-mail reste sur votre serveur (données sensibles détectées) : son contenu n'est jamais envoyé à l'assistant IA.",
  mail_not_allowed:
    "Cet agent n'a pas accès aux e-mails de cet espace : discutez-en avec l'assistant général.",
  label_not_found: "Ce libellé n'existe plus dans le compte.",
  label_already_mapped: "Ce libellé alimente déjà un autre espace : retirez-le d'abord de cet espace.",
  attachment_not_found: "Cette pièce jointe est introuvable dans l'e-mail.",
  attachment_gone: "L'e-mail n'est plus chez le fournisseur : la pièce jointe ne peut plus être importée.",
  // Sending e-mails (task 18)
  approval_stale:
    "Le brouillon ou ses pièces jointes ont changé depuis la demande : rien n'a été envoyé. Préparez de nouveau l'envoi.",
  draft_already_sent: "Cet e-mail est déjà parti : il n'est ni modifié ni renvoyé.",
  draft_changed: "Le brouillon a été modifié : cette approbation ne vaut plus.",
  draft_deleted: "Le brouillon a été supprimé.",
  draft_not_found: "Ce brouillon est introuvable.",
  draft_gone: "Ce brouillon n'existe plus : rien n'a été envoyé.",
  draft_not_uncertain: "Cet envoi n'est pas en état incertain : rien à confirmer.",
  not_sent: "Vous avez indiqué que l'e-mail n'est pas parti.",
  invalid_recipient: "Une adresse de destinataire n'est pas valide.",
  invalid_subject: "L'objet doit tenir sur une ligne.",
  too_many_recipients: "Trop de destinataires (50 au plus).",
  too_many_attachments: "Trop de pièces jointes (10 au plus).",
  body_too_long: "Le message est trop long.",
  recipient_required: "Indiquez au moins un destinataire.",
  mail_empty: "Écrivez un objet ou un message.",
  mail_too_large: "Les pièces jointes sont trop lourdes pour un seul e-mail.",
  mail_send_unavailable:
    "Aucun compte connecté ne peut envoyer d'e-mails dans cet espace : autorisez « Envoyer des e-mails » dans Réglages > Comptes connectés.",
  attachment_unavailable: "Ce document ne peut pas être joint : son original n'est plus disponible.",
  attachment_changed: "Une pièce jointe a changé depuis la demande : rien n'a été envoyé.",
};

export function problemMessage(code: string, fallback: string): string {
  return MESSAGES[code] ?? fallback;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return problemMessage(error.code, error.message);
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "Opération annulée ou refusée par l'appareil.";
  }
  if (error instanceof Error) return error.message;
  return "Erreur inattendue.";
}
