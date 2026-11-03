// Pure helpers for connected accounts (ADR 0012): what each account may do, and where.
// No React here so that everything is unit-testable.

import type {
  ConnectorAccount,
  ConnectorCapability,
  ConnectorGrant,
  ConnectorProvider,
  ConnectorStatus,
  DisconnectResult,
} from "./types";

export const CAPABILITIES: readonly ConnectorCapability[] = [
  "calendar.read",
  "calendar.write",
  "mail.read",
  "mail.send",
];

export const CAPABILITY_LABELS: Record<ConnectorCapability, string> = {
  "calendar.read": "Lire l'agenda",
  "calendar.write": "Modifier l'agenda",
  "mail.read": "Lire les e-mails",
  "mail.send": "Envoyer des e-mails",
};

export const STATUS_LABELS: Record<ConnectorStatus, string> = {
  active: "Connecté",
  reauth_required: "Reconnexion nécessaire",
  revoked: "Déconnecté",
};

export const STATUS_TONES: Record<ConnectorStatus, "teal" | "amber" | "neutral"> = {
  active: "teal",
  reauth_required: "amber",
  revoked: "neutral",
};

/** Query keys of GET /v1/connectors/accounts and /v1/connectors/providers. */
export const ACCOUNTS_KEY = ["connector-accounts"] as const;
export const PROVIDERS_KEY = ["connector-providers"] as const;

/** Name shown for a provider: its configured label, else a readable form of its id. */
export function providerLabel(providers: readonly ConnectorProvider[] | undefined, name: string): string {
  const known = providers?.find((p) => p.name === name)?.label;
  if (known) return known;
  return name === "google" ? "Google" : name.charAt(0).toUpperCase() + name.slice(1);
}

export function isCapability(value: string): value is ConnectorCapability {
  return (CAPABILITIES as readonly string[]).includes(value);
}

export function capabilityLabel(capability: string): string {
  return isCapability(capability) ? CAPABILITY_LABELS[capability] : capability;
}

/** "a", "a et b", "a, b et c" (lower-case after the first item). */
export function capabilityList(capabilities: readonly string[]): string {
  const labels = capabilities.map((c, i) => (i === 0 ? capabilityLabel(c) : lowerFirst(capabilityLabel(c))));
  if (labels.length <= 1) return labels[0] ?? "";
  return `${labels.slice(0, -1).join(", ")} et ${labels[labels.length - 1]}`;
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

/** Writing the calendar implies reading it (same rule as the server); canonical order. */
export function withImplied(capabilities: Iterable<ConnectorCapability>): ConnectorCapability[] {
  const wanted = new Set(capabilities);
  if (wanted.has("calendar.write")) wanted.add("calendar.read");
  return CAPABILITIES.filter((c) => wanted.has(c));
}

/**
 * Ticks or unticks one capability, keeping the server's rule: writing the calendar implies
 * reading it, so unticking the reading also unticks the writing.
 */
export function toggleCapability(
  current: Iterable<ConnectorCapability>,
  capability: ConnectorCapability,
  on: boolean,
): ConnectorCapability[] {
  const next = new Set(current);
  if (on) {
    next.add(capability);
  } else {
    next.delete(capability);
    if (capability === "calendar.read") next.delete("calendar.write");
  }
  return withImplied(next);
}

/** Consents of an account in one space, in canonical order. */
export function grantsIn(account: ConnectorAccount, spaceId: string): ConnectorGrant[] {
  const order = (g: ConnectorGrant) => CAPABILITIES.indexOf(g.capability);
  return account.grants.filter((g) => g.space_id === spaceId).sort((a, b) => order(a) - order(b));
}

/**
 * Capabilities the provider has not granted yet: they need a new consent on its page
 * (incremental consent) instead of a simple authorisation in LifeOS.
 */
export function needsConsent(
  account: ConnectorAccount,
  capabilities: Iterable<ConnectorCapability>,
): ConnectorCapability[] {
  return withImplied(capabilities).filter((c) => !account.capabilities.includes(c));
}

/**
 * Capabilities to ask again when reconnecting: exactly what the provider had granted. A
 * reconnection restores the tokens only and never adds a consent in a space.
 */
export function reconnectCapabilities(account: ConnectorAccount): ConnectorCapability[] {
  return withImplied(account.capabilities);
}

export interface CallbackParams {
  state: string;
  code: string | null;
  error: string | null;
}

/** What the provider put in the return URL; null when it is not a consent's return. */
export function callbackParams(search: URLSearchParams): CallbackParams | null {
  const state = search.get("state");
  if (!state) return null;
  const code = search.get("code");
  const error = search.get("error");
  if (!code && !error) return null;
  return { state, code: code || null, error: error || null };
}

/** Advice after a disconnection, when LifeOS could not revoke its access at the provider. */
export function revocationAdvice(result: DisconnectResult, label: string): string | null {
  const settings = "des applications autorisées dans les paramètres de sécurité du compte.";
  if (result.remote_revocation === "failed") {
    return (
      `L'accès n'a pas pu être révoqué chez ${label} : les jetons sont effacés de LifeOS, ` +
      `mais retirez aussi LifeOS ${settings}`
    );
  }
  if (result.remote_revocation === "manual_required") {
    return `LifeOS n'avait plus de jeton pour révoquer son accès chez ${label} : retirez LifeOS ${settings}`;
  }
  return null;
}
