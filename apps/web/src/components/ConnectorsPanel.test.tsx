import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { ConnectorAccount, ConnectorProvider, Space } from "@/lib/types";

import { ConnectorsPanel } from "./ConnectorsPanel";

const loginWithPasskey = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey }));

const spaces: Space[] = [
  { id: "perso", kind: "personal", name: "Personnel" },
  { id: "pro", kind: "professional", name: "Travail" },
];
const google: ConnectorProvider[] = [
  { name: "google", label: "Google", capabilities: ["calendar.read", "calendar.write", "mail.read", "mail.send"] },
];

function account(overrides: Partial<ConnectorAccount> = {}): ConnectorAccount {
  return {
    id: "a1",
    provider: "google",
    email: "agenda.demo@example.org",
    status: "active",
    capabilities: ["calendar.read", "mail.read"],
    grants: [{ id: "g1", space_id: "perso", capability: "calendar.read", created_at: "", expires_at: null }],
    sources: [],
    connected_at: "2026-10-20T08:00:00Z",
    revoked_at: null,
    last_error: null,
    ...overrides,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Handler = (init: RequestInit) => Response;

/** fetch answering by "METHOD path"; the handlers of a key are used in turn (last one kept). */
function mockFetch(routes: Record<string, Handler | Handler[]>) {
  const used: Record<string, number> = {};
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const key = `${init.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (!route) return json(404, { title: "Not Found", status: 404, code: "not_found" });
    const handlers = Array.isArray(route) ? route : [route];
    const handler = handlers[Math.min(used[key] ?? 0, handlers.length - 1)];
    used[key] = (used[key] ?? 0) + 1;
    if (!handler) throw new Error(`no handler for ${key}`);
    return handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof mockFetch>, key: string): RequestInit[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => `${init?.method ?? "GET"} ${url}` === key)
    .map(([, init]) => init ?? {});
}

const reauth = () => json(403, { title: "Forbidden", status: 403, code: "reauthentication_required" });

function renderPanel() {
  const navigate = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SpaceProvider spaces={spaces}>
        <ConnectorsPanel navigate={navigate} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
  return navigate;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  loginWithPasskey.mockClear();
});

describe("ConnectorsPanel", () => {
  it("shows what each account may do, space by space", async () => {
    mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": () => json(200, [account()]),
    });
    renderPanel();

    expect(await screen.findByText("agenda.demo@example.org")).toBeTruthy();
    expect(screen.getByText("Connecté")).toBeTruthy();
    expect(screen.getByText(/Accordé chez Google : Lire l'agenda et lire les e-mails/)).toBeTruthy();
    const personal = within(screen.getByRole("group", { name: "Espace « Personnel »" }));
    expect((personal.getByRole("checkbox", { name: "Lire l'agenda" }) as HTMLInputElement).checked).toBe(true);
    expect((personal.getByRole("checkbox", { name: "Lire les e-mails" }) as HTMLInputElement).checked).toBe(false);
    // Not granted by Google yet: ticking it means a new consent there.
    expect(personal.getByRole("checkbox", { name: "Envoyer des e-mails (accord chez Google)" })).toBeTruthy();
    const work = within(screen.getByRole("group", { name: "Espace « Travail »" }));
    expect(work.getByText("Aucun usage dans cet espace.")).toBeTruthy();
    expect(work.getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(true);
  });

  it("authorises another space after a passkey confirmation, and withdraws at once", async () => {
    const fetchMock = mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": () => json(200, [account()]),
      "PUT /v1/connectors/accounts/a1/grants": [
        reauth,
        () =>
          json(
            200,
            account({
              grants: [
                ...account().grants,
                { id: "g2", space_id: "pro", capability: "mail.read", created_at: "", expires_at: null },
              ],
            }),
          ),
        () => json(200, account({ grants: [] })),
      ],
    });
    renderPanel();

    const work = within(await screen.findByRole("group", { name: "Espace « Travail »" }));
    fireEvent.click(work.getByRole("checkbox", { name: "Lire les e-mails" }));
    await waitFor(() =>
      expect((work.getByRole("checkbox", { name: "Lire les e-mails" }) as HTMLInputElement).checked).toBe(true),
    );
    expect(loginWithPasskey).toHaveBeenCalledOnce();
    const puts = calls(fetchMock, "PUT /v1/connectors/accounts/a1/grants");
    expect(JSON.parse(String(puts[1]?.body))).toEqual({ space_id: "pro", capabilities: ["mail.read"] });

    // Unticking the reading of the calendar also withdraws its writing; no passkey asked.
    const personal = within(screen.getByRole("group", { name: "Espace « Personnel »" }));
    fireEvent.click(personal.getByRole("checkbox", { name: "Lire l'agenda" }));
    await waitFor(() =>
      expect((personal.getByRole("checkbox", { name: "Lire l'agenda" }) as HTMLInputElement).checked).toBe(false),
    );
    expect(JSON.parse(String(calls(fetchMock, "PUT /v1/connectors/accounts/a1/grants")[2]?.body))).toEqual({
      space_id: "perso",
      capabilities: [],
    });
    expect(loginWithPasskey).toHaveBeenCalledOnce();
  });

  it("asks the provider for a capability it has not granted yet", async () => {
    const fetchMock = mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": () => json(200, [account()]),
      "POST /v1/connectors/oauth/start": () => json(200, { authorization_url: "https://accounts.example/consent?x=1" }),
    });
    const navigate = renderPanel();

    const personal = within(await screen.findByRole("group", { name: "Espace « Personnel »" }));
    fireEvent.click(personal.getByRole("checkbox", { name: "Envoyer des e-mails (accord chez Google)" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://accounts.example/consent?x=1"));
    const [start] = calls(fetchMock, "POST /v1/connectors/oauth/start");
    expect(JSON.parse(String(start?.body))).toEqual({
      provider: "google",
      space_id: "perso",
      capabilities: ["mail.send"],
      account_id: "a1",
    });
    expect(calls(fetchMock, "PUT /v1/connectors/accounts/a1/grants")).toEqual([]);
  });

  it("offers to reconnect an account the provider no longer accepts", async () => {
    // Like the server: a suspended account keeps the scopes and the consents it had.
    const suspended = account({
      status: "reauth_required",
      last_error: "invalid_grant",
      grants: [
        ...account().grants,
        { id: "g2", space_id: "pro", capability: "mail.read", created_at: "", expires_at: null },
      ],
    });
    const fetchMock = mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": () => json(200, [suspended]),
      "POST /v1/connectors/oauth/start": [reauth, () => json(200, { authorization_url: "https://accounts.example/again" })],
    });
    const navigate = renderPanel();

    expect(await screen.findByText("Reconnexion nécessaire")).toBeTruthy();
    expect(screen.getByText(/Ses usages sont suspendus jusqu'à la reconnexion/)).toBeTruthy();
    // Its consents can still be withdrawn, but no new one is given before reconnection.
    const work = within(screen.getByRole("group", { name: "Espace « Travail »" }));
    expect((work.getByRole("checkbox", { name: "Lire l'agenda" }) as HTMLInputElement).disabled).toBe(true);
    const personal = within(screen.getByRole("group", { name: "Espace « Personnel »" }));
    expect((personal.getByRole("checkbox", { name: "Lire l'agenda" }) as HTMLInputElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Reconnecter le compte" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://accounts.example/again"));
    expect(loginWithPasskey).toHaveBeenCalledOnce();
    // Tokens only: what Google had granted, as a reconnection that adds no consent.
    const starts = calls(fetchMock, "POST /v1/connectors/oauth/start");
    expect(JSON.parse(String(starts[1]?.body))).toEqual({
      provider: "google",
      space_id: "perso",
      capabilities: ["calendar.read", "mail.read"],
      account_id: "a1",
      reconnect: true,
    });
  });

  it("tells to withdraw the access at the provider when disconnecting a suspended account", async () => {
    mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": () =>
        json(200, [account({ status: "reauth_required", last_error: "token_unreadable" })]),
      "DELETE /v1/connectors/accounts/a1?purge=false": () =>
        json(200, { account_id: "a1", remote_revocation: "manual_required", purged: false }),
    });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Déconnecter…" }));
    const confirm = within(screen.getByRole("group", { name: "Déconnecter agenda.demo@example.org ?" }));
    expect(confirm.getByText(/ne peut donc pas révoquer son accès chez Google/)).toBeTruthy();
    expect(confirm.queryByText(/LifeOS révoque son accès/)).toBeNull();
    fireEvent.click(confirm.getByRole("button", { name: "Déconnecter agenda.demo@example.org" }));
    expect(await screen.findByText(/LifeOS n'avait plus de jeton pour révoquer son accès chez Google/)).toBeTruthy();
  });

  it("asks whether to keep the synced data before disconnecting, keeping it by default", async () => {
    const fetchMock = mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": [
        () => json(200, [account()]),
        () => json(200, [account({ status: "revoked", grants: [], revoked_at: "2026-10-20T09:00:00Z" })]),
      ],
      "DELETE /v1/connectors/accounts/a1?purge=false": () =>
        json(200, { account_id: "a1", remote_revocation: "failed", purged: false }),
    });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Déconnecter…" }));
    const confirm = within(screen.getByRole("group", { name: "Déconnecter agenda.demo@example.org ?" }));
    expect((confirm.getByRole("radio", { name: /Les garder/ }) as HTMLInputElement).checked).toBe(true);
    expect((confirm.getByRole("radio", { name: /Les supprimer aussi/ }) as HTMLInputElement).checked).toBe(false);

    // Cancelling changes nothing.
    fireEvent.click(confirm.getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("group", { name: /Déconnecter agenda/ })).toBeNull();
    expect(calls(fetchMock, "DELETE /v1/connectors/accounts/a1?purge=false")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Déconnecter…" }));
    fireEvent.click(screen.getByRole("button", { name: "Déconnecter agenda.demo@example.org" }));
    expect(await screen.findByText(/est déconnecté. Les données déjà synchronisées sont conservées/)).toBeTruthy();
    // Google could not be reached: the owner is told to withdraw the access there too.
    expect(screen.getByText(/retirez aussi LifeOS des applications autorisées/)).toBeTruthy();
    expect(await screen.findByText("Déconnecté")).toBeTruthy();
    expect(loginWithPasskey).not.toHaveBeenCalled();
  });

  it("deletes the synced data when the owner chooses so", async () => {
    const fetchMock = mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": [() => json(200, [account()]), () => json(200, [])],
      "DELETE /v1/connectors/accounts/a1?purge=true": () =>
        json(200, { account_id: "a1", remote_revocation: "done", purged: true }),
    });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Déconnecter…" }));
    fireEvent.click(screen.getByRole("radio", { name: /Les supprimer aussi/ }));
    fireEvent.click(screen.getByRole("button", { name: "Déconnecter agenda.demo@example.org" }));
    expect(await screen.findByText(/les données synchronisées depuis ce compte sont supprimées/)).toBeTruthy();
    expect(calls(fetchMock, "DELETE /v1/connectors/accounts/a1?purge=true")).toHaveLength(1);
    expect(await screen.findByText(/Aucun compte connecté/)).toBeTruthy();
  });

  it("connects a new account with the least access by default", async () => {
    const fetchMock = mockFetch({
      "GET /v1/connectors/providers": () => json(200, google),
      "GET /v1/connectors/accounts": () => json(200, []),
      "POST /v1/connectors/oauth/start": () => json(200, { authorization_url: "https://accounts.example/new" }),
    });
    const navigate = renderPanel();

    expect(await screen.findByText(/LifeOS n'accède à aucun agenda ni aucune boîte e-mail/)).toBeTruthy();
    const usages = within(screen.getByRole("group", { name: "Usages autorisés dans cet espace" }));
    expect((usages.getByRole("checkbox", { name: "Lire l'agenda" }) as HTMLInputElement).checked).toBe(true);

    // Nothing ticked: explained, nothing sent.
    fireEvent.click(usages.getByRole("checkbox", { name: "Lire l'agenda" }));
    fireEvent.click(screen.getByRole("button", { name: "Connecter un compte Google" }));
    expect(screen.getByText("Cochez au moins un usage à autoriser.")).toBeTruthy();
    expect(calls(fetchMock, "POST /v1/connectors/oauth/start")).toEqual([]);

    // Writing the calendar brings its reading along.
    fireEvent.click(usages.getByRole("checkbox", { name: "Modifier l'agenda" }));
    expect((usages.getByRole("checkbox", { name: "Lire l'agenda" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByLabelText("Espace"), { target: { value: "pro" } });
    fireEvent.click(screen.getByRole("button", { name: "Connecter un compte Google" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://accounts.example/new"));
    const [start] = calls(fetchMock, "POST /v1/connectors/oauth/start");
    expect(JSON.parse(String(start?.body))).toEqual({
      provider: "google",
      space_id: "pro",
      capabilities: ["calendar.read", "calendar.write"],
    });
  });

  it("says when the server has no provider configured", async () => {
    mockFetch({
      "GET /v1/connectors/providers": () => json(200, []),
      "GET /v1/connectors/accounts": () => json(200, []),
    });
    renderPanel();

    expect(await screen.findByText(/La connexion de comptes n'est pas activée sur ce serveur/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Connecter un compte/ })).toBeNull();
  });
});
