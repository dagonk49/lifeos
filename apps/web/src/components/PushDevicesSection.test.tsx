import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PushDevice } from "@/lib/types";

import { PushDevicesSection } from "./PushDevicesSection";

const loginWithPasskey = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey }));

const KEY = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4";
const ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36";
const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";

function device(fields: Partial<PushDevice> = {}): PushDevice {
  return {
    id: "d1",
    label: "Android · Chrome",
    preview: "generic",
    user_agent: ANDROID,
    created_at: "2026-10-28T08:00:00Z",
    consented_at: "2026-10-28T08:00:00Z",
    last_success_at: null,
    failure_count: 0,
    current: true,
    ...fields,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string; body: unknown };

/** A browser with the Push API: the PushManager is a mock. */
function stubPushManager(ua: string, existing: boolean) {
  const subscription = {
    toJSON: () => ({ endpoint: "https://fcm.googleapis.com/fcm/send/abc", keys: { p256dh: "p", auth: "a" } }),
    unsubscribe: vi.fn(async () => true),
    options: { applicationServerKey: null },
  };
  let current: typeof subscription | null = existing ? subscription : null;
  const pushManager = {
    getSubscription: vi.fn(async () => current),
    subscribe: vi.fn(async () => {
      current = subscription;
      return subscription;
    }),
  };
  const registration = { pushManager };
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("Notification", { requestPermission: vi.fn(async () => "granted") });
  vi.stubGlobal("navigator", {
    userAgent: ua,
    maxTouchPoints: ua === IPHONE ? 5 : 0,
    serviceWorker: {
      register: vi.fn(async () => registration),
      ready: Promise.resolve(registration),
      getRegistration: vi.fn(async () => registration),
    },
  });
  return { pushManager, subscription };
}

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
      <PushDevicesSection />
    </QueryClientProvider>,
  );
  return calls;
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  loginWithPasskey.mockClear();
});

describe("PushDevicesSection", () => {
  it("enables push on this device from a click and lists it", async () => {
    const { pushManager } = stubPushManager(ANDROID, false);
    let devices: PushDevice[] = [];
    const calls = setup((call) => {
      if (call.url.endsWith("/push/config")) return json(200, { enabled: true, public_key: KEY });
      if (call.method === "POST") {
        devices = [device()];
        return json(201, devices[0]);
      }
      return json(200, devices);
    });
    expect(await screen.findByText("Aucun appareil pour l'instant.")).toBeTruthy();
    expect(Notification.requestPermission).not.toHaveBeenCalled(); // never on page load
    fireEvent.click(await screen.findByRole("button", { name: "Activer sur cet appareil" }));
    await screen.findByText("Notifications activées sur cet appareil.");
    expect(Notification.requestPermission).toHaveBeenCalled();
    expect(pushManager.subscribe).toHaveBeenCalledWith(expect.objectContaining({ userVisibleOnly: true }));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      endpoint: "https://fcm.googleapis.com/fcm/send/abc",
      keys: { p256dh: "p", auth: "a" },
      label: "Android · Chrome",
    });
    expect(await screen.findByText("Cet appareil")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Désactiver sur cet appareil" })).toBeTruthy();
  });

  it("sends a test, revokes a device and asks a passkey to show titles", async () => {
    stubPushManager(ANDROID, true);
    let devices = [device({ last_success_at: "2026-10-28T09:00:00Z" }), device({ id: "d2", label: "Tablette", current: false })];
    let reauthenticated = false;
    const calls = setup((call) => {
      if (call.url.endsWith("/push/config")) return json(200, { enabled: true, public_key: KEY });
      if (call.url.endsWith("/test")) return json(202, { queued: true });
      if (call.method === "DELETE") {
        devices = devices.filter((d) => !call.url.endsWith(d.id));
        return json(204, null);
      }
      if (call.method === "PATCH") {
        if (!reauthenticated) {
          reauthenticated = true;
          return json(403, { code: "reauthentication_required", title: "Recent authentication required" });
        }
        devices = devices.map((d) => (call.url.endsWith(d.id) ? { ...d, preview: "full" } : d));
        return json(200, devices[0]);
      }
      return json(200, devices);
    });
    expect(await screen.findByText(/Dernier envoi réussi le/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Envoyer un test à « Tablette »" }));
    await screen.findByText("Notification de test envoyée à « Tablette » : elle arrive dans la minute.");

    fireEvent.click(screen.getByLabelText("Afficher le titre sur l'écran verrouillé de « Android · Chrome »"));
    await waitFor(() => expect(loginWithPasskey).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Afficher le titre sur l'écran verrouillé de « Android · Chrome »") as HTMLInputElement)
          .checked,
      ).toBe(true),
    );
    expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([
      { preview: "full" },
      { preview: "full" },
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Révoquer « Tablette »" }));
    await screen.findByText("« Tablette » ne recevra plus de notifications.");
    await waitFor(() => expect(screen.queryByText("Tablette")).toBeNull());
  });

  it("asks iPhone users to add LifeOS to the home screen first", async () => {
    stubPushManager(IPHONE, false);
    setup((call) =>
      call.url.endsWith("/push/config") ? json(200, { enabled: true, public_key: KEY }) : json(200, []),
    );
    expect(await screen.findByText(/ajoutez d'abord LifeOS à l'écran d'accueil/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Activer sur cet appareil" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says when push is not configured on the server", async () => {
    stubPushManager(ANDROID, false);
    setup(() => json(503, { code: "push_not_configured", title: "Push notifications are not configured" }));
    expect(
      await screen.findByText("Les notifications sur le téléphone ne sont pas activées sur ce serveur."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Activer sur cet appareil" })).toBeNull();
  });
});
