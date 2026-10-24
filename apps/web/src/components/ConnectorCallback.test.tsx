import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { OAuthResult, Space } from "@/lib/types";

import { ConnectorCallback } from "./ConnectorCallback";

const spaces: Space[] = [{ id: "perso", kind: "personal", name: "Personnel" }];

const result: OAuthResult = {
  account: {
    id: "a1",
    provider: "google",
    email: "agenda.demo@example.org",
    status: "active",
    capabilities: ["calendar.read"],
    grants: [{ id: "g1", space_id: "perso", capability: "calendar.read", created_at: "", expires_at: null }],
    sources: [],
    connected_at: "2026-10-20T08:00:00Z",
    revoked_at: null,
    last_error: null,
  },
  space_id: "perso",
  requested: ["calendar.read", "mail.read"],
  granted: ["calendar.read"],
  missing: ["mail.read"],
  reconnected: false,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** The provider sends the browser back here with these query parameters. */
function arrive(query: string) {
  window.history.replaceState(null, "", `/connexions/retour?${query}`);
  render(
    <StrictMode>
      <QueryClientProvider client={new QueryClient()}>
        <SpaceProvider spaces={spaces}>
          <ConnectorCallback search={new URLSearchParams(window.location.search)} />
        </SpaceProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("ConnectorCallback", () => {
  it("hands the code to the API once, after removing it from the address bar", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, result));
    vi.stubGlobal("fetch", fetchMock);
    arrive("state=s-123&code=c-456&scope=openid");

    expect(await screen.findByText("agenda.demo@example.org est connecté.")).toBeTruthy();
    expect(window.location.pathname).toBe("/connexions/retour");
    expect(window.location.search).toBe("");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/connectors/oauth/callback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ state: "s-123", code: "c-456", error: null });
    // What was actually granted, and what the owner unticked at the provider.
    expect(screen.getByText(/Autorisé dans l'espace « Personnel » : Lire l'agenda\./)).toBeTruthy();
    expect(screen.getByText(/Non accordé chez le fournisseur : Lire les e-mails/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Revenir aux comptes connectés" }).getAttribute("href")).toBe(
      "/settings#connexions",
    );
  });

  it("says that a reconnection leaves the consents per space unchanged", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { ...result, reconnected: true })));
    arrive("state=s-123&code=c-456");

    expect(await screen.findByText("agenda.demo@example.org est reconnecté.")).toBeTruthy();
    expect(screen.getByText("Ses autorisations par espace sont inchangées.")).toBeTruthy();
    expect(screen.queryByText(/Autorisé dans l'espace/)).toBeNull();
    expect(screen.getByText(/Non accordé chez le fournisseur cette fois : Lire les e-mails/)).toBeTruthy();
  });

  it("refuses another account than the one to connect", async () => {
    const problem = { title: "Conflict", status: 409, code: "connector_account_mismatch" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(409, problem)));
    arrive("state=s-123&code=c-456");

    expect(await screen.findByText(/Un autre compte a été choisi chez le fournisseur/)).toBeTruthy();
  });

  it("reports a refusal at the provider", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(json(400, { title: "Bad Request", status: 400, code: "connector_consent_denied" }));
    vi.stubGlobal("fetch", fetchMock);
    arrive("state=s-123&error=access_denied");

    expect(await screen.findByText(/L'accès n'a pas été accordé chez le fournisseur/)).toBeTruthy();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ state: "s-123", code: null, error: "access_denied" });
    expect(window.location.search).toBe("");
  });

  it("sends nothing for an incomplete return", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    arrive("code=c-456");

    expect(screen.getByText(/Ce retour de connexion est incomplet/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
  });
});
