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
  llm_budget_exceeded: "Le budget mensuel de l'assistant IA est atteint.",
  llm_blocked: "Le fournisseur IA a refusé de répondre à cette demande.",
  llm_invalid_output: "La réponse de l'IA n'a pas pu être validée. Réessayez.",
  approval_required: "Cette action demande votre approbation explicite.",
  reauthentication_required: "Confirmez votre identité avec votre passkey.",
  version_conflict: "Cet élément a été modifié ailleurs : rechargez la page.",
  memory_protected: "Ce souvenir est protégé : confirmez la suppression.",
  last_passkey: "Impossible de révoquer votre dernière passkey.",
  passkey_verification_failed: "La vérification de la passkey a échoué.",
  enrollment_token_invalid: "Lien d'enrôlement invalide ou expiré.",
  validation_error: "Certaines valeurs saisies sont invalides.",
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
