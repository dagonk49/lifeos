import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NotificationPreferences as Preferences } from "@/lib/types";

import { NotificationPreferences } from "./NotificationPreferences";

const loginWithPasskey = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey }));

const defaults: Preferences = {
  categories: [
    { category: "important", enabled: true, max_per_day: null, windows: [], digest_times: [] },
    { category: "suggestion", enabled: true, max_per_day: 3, windows: [], digest_times: ["18:00"] },
  ],
  critical_bypass_quiet_hours: false,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { url: string; method: string; body: unknown };

function setup(handler: (call: Call) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const call = { url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null };
      calls.push(call);
      return handler(call);
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NotificationPreferences />
    </QueryClientProvider>,
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  loginWithPasskey.mockClear();
});

describe("NotificationPreferences", () => {
  it("shows the defaults and saves a new digest time and window", async () => {
    const calls = setup((call) => (call.method === "PUT" ? json(200, defaults) : json(200, defaults)));
    expect(await screen.findByLabelText("Heure de regroupement 1")).toHaveProperty("value", "18:00");
    fireEvent.click(screen.getByRole("button", { name: "Ajouter un créneau" }));
    fireEvent.change(screen.getByLabelText("Heure de regroupement 1"), { target: { value: "19:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer les notifications" }));
    await screen.findByText("Enregistré");
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({
      categories: [
        { category: "important", enabled: true, max_per_day: null, windows: [{ start: "09:00", end: "12:00" }] },
        { category: "suggestion", enabled: true, max_per_day: 3, digest_times: ["19:30"] },
      ],
    });
  });

  it("refuses overlapping windows before sending anything", async () => {
    const calls = setup(() => json(200, defaults));
    await screen.findByLabelText("Heure de regroupement 1");
    fireEvent.click(screen.getByRole("button", { name: "Ajouter un créneau" }));
    fireEvent.click(screen.getByRole("button", { name: "Ajouter un créneau" }));
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer les notifications" }));
    expect(await screen.findByText("Les créneaux ne doivent pas se chevaucher.")).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("confirms with a passkey before letting critical alerts through", async () => {
    let reauthenticated = false;
    const calls = setup((call) => {
      if (call.method === "PATCH") {
        if (!reauthenticated) {
          reauthenticated = true;
          return json(403, { code: "reauthentication_required", title: "Reauthentication required" });
        }
        return json(200, { critical_bypass_quiet_hours: true });
      }
      return json(200, defaults);
    });
    const toggle = await screen.findByRole("switch", {
      name: "Laisser passer les alertes critiques pendant la tranquillité",
    });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(toggle);
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(loginWithPasskey).toHaveBeenCalledTimes(1);
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ critical_bypass_quiet_hours: true });
  });
});
