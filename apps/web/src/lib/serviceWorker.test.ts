// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { GET } from "@/app/sw.js/route";

import { SERVICE_WORKER_SOURCE } from "./serviceWorker";

type Listener = (event: Record<string, unknown>) => void;

/** Runs the worker's source against a fake service worker global. */
function load(options: { clients?: { url: string; focus: () => Promise<unknown> }[] } = {}) {
  const listeners = new Map<string, Listener>();
  const shown: { title: string; options: Record<string, unknown> }[] = [];
  const opened: string[] = [];
  const fetches: { url: string; init: RequestInit }[] = [];
  const self = {
    location: { origin: "https://lifeos.example" },
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    skipWaiting: vi.fn(),
    crypto: { randomUUID: () => "key-1" },
    registration: {
      showNotification: vi.fn(async (title: string, opts: Record<string, unknown>) => {
        shown.push({ title, options: opts });
      }),
      pushManager: { subscribe: vi.fn() },
    },
    clients: {
      claim: vi.fn(async () => undefined),
      matchAll: vi.fn(async () => options.clients ?? []),
      openWindow: vi.fn(async (url: string) => {
        opened.push(url);
        return null;
      }),
    },
  };
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    fetches.push({ url, init });
    return new Response(null, { status: 201 });
  });
  new Function("self", "fetch", SERVICE_WORKER_SOURCE)(self, fetch);
  async function dispatch(type: string, event: Record<string, unknown>) {
    let pending: Promise<unknown> = Promise.resolve();
    listeners.get(type)?.({ ...event, waitUntil: (p: Promise<unknown>) => (pending = p) });
    await pending;
  }
  return { listeners, shown, opened, fetches, dispatch, self };
}

function data(value: unknown) {
  return { json: () => (typeof value === "string" ? JSON.parse(value) : value) };
}

describe("service worker", () => {
  it("handles only push events: never fetch, so it caches nothing", () => {
    const { listeners } = load();
    expect([...listeners.keys()].sort()).toEqual([
      "activate",
      "install",
      "notificationclick",
      "push",
      "pushsubscriptionchange",
    ]);
    expect(SERVICE_WORKER_SOURCE).not.toMatch(/caches|importScripts|addEventListener\("fetch"/);
  });

  it("shows the payload as is, without calling the API", async () => {
    const { shown, fetches, dispatch } = load();
    await dispatch("push", {
      data: data({ title: "LifeOS", body: "Nouveau rappel", url: "/tasks", tag: "lifeos-1" }),
    });
    expect(shown).toEqual([
      {
        title: "LifeOS",
        options: expect.objectContaining({ body: "Nouveau rappel", tag: "lifeos-1", data: { url: "/tasks" } }),
      },
    ]);
    expect(fetches).toEqual([]);
  });

  it("falls back on a generic text for a missing or broken payload", async () => {
    const { shown, dispatch } = load();
    await dispatch("push", { data: null });
    await dispatch("push", { data: { json: () => JSON.parse("{") } });
    expect(shown.map((s) => [s.title, s.options.body])).toEqual([
      ["LifeOS", "Nouvelle notification"],
      ["LifeOS", "Nouvelle notification"],
    ]);
  });

  it("opens same-origin paths only", async () => {
    const { shown, opened, dispatch } = load();
    await dispatch("push", { data: data({ body: "x", url: "https://evil.example/phish" }) });
    expect(shown[0]?.options.data).toEqual({ url: "/" });
    const close = vi.fn();
    await dispatch("notificationclick", { notification: { close, data: { url: "//evil.example/x" } } });
    await dispatch("notificationclick", { notification: { close, data: { url: "/settings#connexions" } } });
    expect(close).toHaveBeenCalledTimes(2);
    expect(opened).toEqual(["https://lifeos.example/", "https://lifeos.example/settings#connexions"]);
  });

  it("focuses an open LifeOS window instead of opening another one", async () => {
    const navigate = vi.fn(async () => null);
    const focus = vi.fn(async () => ({ navigate }));
    const { opened, dispatch } = load({
      clients: [
        { url: "https://other.example/", focus: vi.fn() },
        { url: "https://lifeos.example/chat", focus },
      ],
    });
    await dispatch("notificationclick", { notification: { close: vi.fn(), data: { url: "/tasks" } } });
    expect(focus).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("https://lifeos.example/tasks");
    expect(opened).toEqual([]);
  });

  it("sends a renewed subscription for the device it replaces, never a new one", async () => {
    const { fetches, dispatch } = load();
    const fresh = { toJSON: () => ({ endpoint: "https://fcm.googleapis.com/new", keys: { p256dh: "p", auth: "a" } }) };
    await dispatch("pushsubscriptionchange", {
      oldSubscription: { endpoint: "https://fcm.googleapis.com/old" },
      newSubscription: fresh,
    });
    expect(fetches).toHaveLength(1);
    const [{ url, init }] = fetches as [{ url: string; init: RequestInit }];
    expect(url).toBe("/v1/notifications/devices");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(String(init.body))).toEqual({
      endpoint: "https://fcm.googleapis.com/new",
      keys: { p256dh: "p", auth: "a" },
      previous_endpoint: "https://fcm.googleapis.com/old",
    });
    // No previous subscription: enabling push stays the owner's choice.
    await dispatch("pushsubscriptionchange", { oldSubscription: null, newSubscription: fresh });
    expect(fetches).toHaveLength(1);
  });

  it("is served as JavaScript and never cached", async () => {
    const response = GET();
    expect(response.headers.get("content-type")).toBe("text/javascript; charset=utf-8");
    expect(response.headers.get("cache-control")).toBe("no-cache");
    expect(await response.text()).toBe(SERVICE_WORKER_SOURCE);
  });
});
