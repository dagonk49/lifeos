import { describe, expect, it } from "vitest";

import {
  callbackParams,
  capabilityList,
  grantsIn,
  needsConsent,
  providerLabel,
  reconnectCapabilities,
  revocationAdvice,
  toggleCapability,
  withImplied,
} from "./connectors";
import type { ConnectorAccount } from "./types";

function account(overrides: Partial<ConnectorAccount> = {}): ConnectorAccount {
  return {
    id: "a1",
    provider: "google",
    email: "agenda.demo@example.org",
    status: "active",
    capabilities: ["calendar.read"],
    grants: [
      { id: "g2", space_id: "pro", capability: "calendar.read", created_at: "", expires_at: null },
    ],
    sources: [],
    connected_at: "2026-10-20T08:00:00Z",
    revoked_at: null,
    last_error: null,
    ...overrides,
  };
}

describe("connectors helpers", () => {
  it("adds calendar reading to calendar writing, in canonical order", () => {
    expect(withImplied(["mail.send", "calendar.write"])).toEqual(["calendar.read", "calendar.write", "mail.send"]);
    expect(withImplied([])).toEqual([]);
  });

  it("ticks and unticks capabilities like the server: no calendar writing without reading", () => {
    expect(toggleCapability([], "calendar.write", true)).toEqual(["calendar.read", "calendar.write"]);
    expect(toggleCapability(["calendar.read", "calendar.write", "mail.read"], "calendar.read", false)).toEqual([
      "mail.read",
    ]);
    expect(toggleCapability(["calendar.read", "calendar.write"], "calendar.write", false)).toEqual(["calendar.read"]);
    expect(toggleCapability(["mail.read"], "mail.send", true)).toEqual(["mail.read", "mail.send"]);
  });

  it("names providers", () => {
    expect(providerLabel([{ name: "fake", label: "Compte de démonstration", capabilities: [] }], "fake")).toBe(
      "Compte de démonstration",
    );
    expect(providerLabel(undefined, "google")).toBe("Google");
    expect(providerLabel([], "outlook")).toBe("Outlook");
  });

  it("names capabilities in French", () => {
    expect(capabilityList(["calendar.read", "mail.read"])).toBe("Lire l'agenda et lire les e-mails");
    expect(capabilityList(["calendar.read", "calendar.write", "mail.send"])).toBe(
      "Lire l'agenda, modifier l'agenda et envoyer des e-mails",
    );
    expect(capabilityList([])).toBe("");
  });

  it("knows what needs a new consent at the provider", () => {
    const a = account();
    expect(needsConsent(a, ["calendar.read"])).toEqual([]);
    expect(needsConsent(a, ["calendar.write", "mail.read"])).toEqual(["calendar.write", "mail.read"]);
  });

  it("lists the consents of one space and what to ask again on reconnection", () => {
    const a = account({
      grants: [
        { id: "g1", space_id: "perso", capability: "mail.read", created_at: "", expires_at: null },
        { id: "g0", space_id: "perso", capability: "calendar.read", created_at: "", expires_at: null },
      ],
    });
    expect(grantsIn(a, "perso").map((g) => g.id)).toEqual(["g0", "g1"]);
    expect(grantsIn(a, "pro")).toEqual([]);
    // Exactly what the provider had granted: the consents per space never widen it.
    const held = account({
      status: "reauth_required",
      capabilities: ["mail.read"],
      grants: [{ id: "g2", space_id: "pro", capability: "calendar.read", created_at: "", expires_at: null }],
    });
    expect(reconnectCapabilities(held)).toEqual(["mail.read"]);
    expect(reconnectCapabilities(account({ status: "reauth_required", capabilities: [] }))).toEqual([]);
    expect(reconnectCapabilities(account({ capabilities: ["calendar.write"] }))).toEqual([
      "calendar.read",
      "calendar.write",
    ]);
  });

  it("reads the provider's return URL", () => {
    expect(callbackParams(new URLSearchParams("state=s1&code=c1&scope=x"))).toEqual({
      state: "s1",
      code: "c1",
      error: null,
    });
    expect(callbackParams(new URLSearchParams("state=s1&error=access_denied"))).toEqual({
      state: "s1",
      code: null,
      error: "access_denied",
    });
    expect(callbackParams(new URLSearchParams("code=c1"))).toBeNull();
    expect(callbackParams(new URLSearchParams("state=s1"))).toBeNull();
  });

  it("advises to withdraw the access by hand only when LifeOS could not revoke it", () => {
    expect(revocationAdvice({ account_id: "a1", remote_revocation: "done", purged: false }, "Google")).toBeNull();
    expect(revocationAdvice({ account_id: "a1", remote_revocation: "not_needed", purged: true }, "Google")).toBeNull();
    expect(revocationAdvice({ account_id: "a1", remote_revocation: "failed", purged: false }, "Google")).toContain(
      "chez Google",
    );
    expect(
      revocationAdvice({ account_id: "a1", remote_revocation: "manual_required", purged: false }, "Google"),
    ).toMatch(/plus de jeton .* chez Google : retirez LifeOS des applications autorisées/);
  });
});
