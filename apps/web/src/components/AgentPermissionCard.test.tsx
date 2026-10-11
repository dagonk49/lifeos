import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentInfo } from "@/lib/types";

import { AgentPermissionCard } from "./AgentPermissionCard";

const loginWithPasskey = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey }));

const granted: AgentInfo[] = [
  {
    name: "coaching",
    label: "Coaching",
    description: "Fait le point.",
    resources: ["goals", "journal", "reviews"],
    missing_permissions: [],
  },
];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  loginWithPasskey.mockClear();
});

describe("AgentPermissionCard", () => {
  it("grants read access after a passkey confirmation, then offers to resend", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json(403, { title: "Forbidden", status: 403, code: "reauthentication_required" }),
      )
      .mockResolvedValueOnce(json(200, granted));
    vi.stubGlobal("fetch", fetchMock);
    const onResend = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AgentPermissionCard
          need={{ agent: "coaching", spaceId: "s1", missing: ["journal", "reviews"], content: "Où en suis-je ?" }}
          label="Coaching"
          spaceName="Personnel"
          resending={false}
          onResend={onResend}
          onDismiss={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText("journal et bilans")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Renvoyer" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Autoriser" }));

    const resend = await screen.findByRole("button", { name: "Renvoyer" });
    expect(loginWithPasskey).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/v1/agents/coaching/access");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ space_id: "s1", enabled: true });
    expect(screen.getByText(/Accès accordé pour 90 jours/)).toBeTruthy();

    fireEvent.click(resend);
    await waitFor(() => expect(onResend).toHaveBeenCalledOnce());
  });

  it("shows a cancelled passkey ceremony as an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json(403, { title: "Forbidden", status: 403, code: "reauthentication_required" })),
    );
    loginWithPasskey.mockRejectedValueOnce(new DOMException("cancelled", "NotAllowedError"));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AgentPermissionCard
          need={{ agent: "planning", spaceId: "s1", missing: ["tasks"], content: "Planifier" }}
          label="Planification"
          spaceName="Personnel"
          resending={false}
          onResend={() => undefined}
          onDismiss={() => undefined}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Autoriser" }));
    expect(await screen.findByText("Opération annulée ou refusée par l'appareil.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Renvoyer" })).toBeNull();
  });
});
