import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SessionInfo } from "@/lib/types";

import { SessionList } from "./SessionList";

const loginWithPasskey = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey }));

function session(id: string, userAgent: string, current: boolean): SessionInfo {
  return {
    id,
    created_at: "2026-10-20T08:00:00Z",
    authenticated_at: "2026-10-20T08:00:00Z",
    last_seen_at: "2026-10-20T09:00:00Z",
    expires_at: "2026-11-19T08:00:00Z",
    user_agent: userAgent,
    ip: null,
    current,
  };
}

const THIS = session("s1", "Firefox sur Linux", true);
const PHONE = session("s2", "Chrome sur Android", false);
const TABLET = session("s3", "Safari sur iPad", false);
const REAUTH = { title: "Forbidden", status: 403, code: "reauthentication_required" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Answers by "METHOD path", each route with its replies in order (the last one repeats). */
function server(routes: Record<string, Response[]>) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    calls.push(key);
    const replies = routes[key];
    if (!replies) throw new Error(`unexpected request ${key}`);
    const reply = replies.length > 1 ? replies.shift() : replies[0];
    return reply!.clone();
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderList() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SessionList />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  loginWithPasskey.mockReset();
  loginWithPasskey.mockImplementation(async () => ({}));
});

describe("SessionList", () => {
  it("signs out every other session and keeps this one", async () => {
    const calls = server({
      "GET /v1/auth/sessions": [json(200, [THIS, PHONE, TABLET]), json(200, [THIS])],
      "POST /v1/auth/sessions/revoke-others": [json(200, { revoked: 2 })],
    });
    renderList();

    expect(await screen.findByText("Chrome sur Android")).toBeTruthy();
    expect(screen.getByText("Cette session")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Déconnecter les autres sessions" }));

    expect(await screen.findByText("2 autres sessions ont été déconnectées.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Chrome sur Android")).toBeNull());
    expect(screen.getByText("Firefox sur Linux")).toBeTruthy();
    // Nothing left to sign out: the action is no longer offered.
    expect(screen.queryByRole("button", { name: "Déconnecter les autres sessions" })).toBeNull();
    expect(loginWithPasskey).not.toHaveBeenCalled();
    expect(calls).toEqual([
      "GET /v1/auth/sessions",
      "POST /v1/auth/sessions/revoke-others",
      "GET /v1/auth/sessions",
    ]);
  });

  it("asks for the passkey when the login is not recent, then retries", async () => {
    const calls = server({
      "GET /v1/auth/sessions": [json(200, [THIS, PHONE])],
      "POST /v1/auth/sessions/revoke-others": [json(403, REAUTH), json(200, { revoked: 1 })],
    });
    renderList();

    fireEvent.click(await screen.findByRole("button", { name: "Déconnecter les autres sessions" }));

    expect(await screen.findByText("1 autre session a été déconnectée.")).toBeTruthy();
    expect(loginWithPasskey).toHaveBeenCalledOnce();
    expect(calls.filter((call) => call.startsWith("POST"))).toHaveLength(2);
  });

  it("shows a cancelled passkey confirmation as an error, nothing signed out", async () => {
    const calls = server({
      "GET /v1/auth/sessions": [json(200, [THIS, PHONE])],
      "POST /v1/auth/sessions/revoke-others": [json(403, REAUTH)],
    });
    loginWithPasskey.mockRejectedValueOnce(new DOMException("cancelled", "NotAllowedError"));
    renderList();

    fireEvent.click(await screen.findByRole("button", { name: "Déconnecter les autres sessions" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Opération annulée ou refusée par l'appareil.");
    expect(screen.getByText("Chrome sur Android")).toBeTruthy();
    expect(calls.filter((call) => call.startsWith("POST"))).toHaveLength(1);
  });

  it("clears a previous failure when another session is revoked", async () => {
    server({
      "GET /v1/auth/sessions": [json(200, [THIS, PHONE, TABLET]), json(200, [THIS, TABLET])],
      "POST /v1/auth/sessions/revoke-others": [json(403, REAUTH)],
      "DELETE /v1/auth/sessions/s2": [new Response(null, { status: 204 })],
    });
    loginWithPasskey.mockRejectedValueOnce(new DOMException("cancelled", "NotAllowedError"));
    renderList();

    fireEvent.click(await screen.findByRole("button", { name: "Déconnecter les autres sessions" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Révoquer la session Chrome sur Android" }));

    await waitFor(() => expect(screen.queryByText("Chrome sur Android")).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers nothing to sign out when this is the only session", async () => {
    server({ "GET /v1/auth/sessions": [json(200, [THIS])] });
    renderList();

    expect(await screen.findByText("Firefox sur Linux")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Déconnecter les autres sessions" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Révoquer/ })).toBeNull();
  });

  it("revokes one session on its own", async () => {
    const calls = server({
      "GET /v1/auth/sessions": [json(200, [THIS, PHONE, TABLET]), json(200, [THIS, TABLET])],
      "DELETE /v1/auth/sessions/s2": [new Response(null, { status: 204 })],
    });
    renderList();

    fireEvent.click(await screen.findByRole("button", { name: "Révoquer la session Chrome sur Android" }));

    await waitFor(() => expect(screen.queryByText("Chrome sur Android")).toBeNull());
    expect(screen.getByText("Safari sur iPad")).toBeTruthy();
    expect(calls).toContain("DELETE /v1/auth/sessions/s2");
  });
});
