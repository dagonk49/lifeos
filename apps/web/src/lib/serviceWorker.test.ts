// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import { GET } from "@/app/sw.js/route";

import { SERVICE_WORKER_SOURCE } from "./serviceWorker";

type Listener = (event: Record<string, unknown>) => void;

/** Runs the worker's source against a fake service worker global. */
function load(
  options: {
    clients?: { url: string; focus: () => Promise<unknown> }[];
    network?: (request: Request) => Promise<Response>;
  } = {},
) {
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
      navigationPreload: { enable: vi.fn(async () => undefined) },
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
  const fetch = vi.fn(async (url: string | Request, init: RequestInit) => {
    if (typeof url !== "string") {
      if (!options.network) throw new Error("unexpected network request");
      return options.network(url);
    }
    fetches.push({ url, init });
    return new Response(null, { status: 201 });
  });
  new Function("self", "fetch", SERVICE_WORKER_SOURCE)(self, fetch);
  async function dispatch(type: string, event: Record<string, unknown>) {
    let pending: Promise<unknown> = Promise.resolve();
    listeners.get(type)?.({ ...event, waitUntil: (p: Promise<unknown>) => (pending = p) });
    await pending;
  }
  /** A fetch event: what the worker answered (undefined: left to the browser). */
  function request(event: Record<string, unknown>) {
    let response: Promise<Response> | undefined;
    const kept: Promise<unknown>[] = [];
    listeners.get("fetch")?.({
      ...event,
      respondWith: (r: Response | Promise<Response>) => (response = Promise.resolve(r)),
      waitUntil: (p: Promise<unknown>) => kept.push(p),
    });
    return { response, kept };
  }
  return { listeners, shown, opened, fetches, dispatch, request, self, fetch };
}

function data(value: unknown) {
  return { json: () => (typeof value === "string" ? JSON.parse(value) : value) };
}

describe("service worker", () => {
  it("stores nothing: no cache, no database, no imported script", () => {
    const { listeners } = load();
    expect([...listeners.keys()].sort()).toEqual([
      "activate",
      "fetch",
      "install",
      "message",
      "notificationclick",
      "push",
      "pushsubscriptionchange",
    ]);
    expect(SERVICE_WORKER_SOURCE).not.toMatch(/caches|indexedDB|localStorage|importScripts/);
  });

  it("enables navigation preload and takes control on activation", async () => {
    const { dispatch, self } = load();
    await dispatch("activate", {});
    expect(self.clients.claim).toHaveBeenCalled();
    expect(self.registration.navigationPreload.enable).toHaveBeenCalled();
  });

  it("leaves scripts, API calls and other origins to the browser", () => {
    const { request, fetch } = load();
    for (const event of [
      { request: { mode: "cors", method: "GET", url: "https://lifeos.example/v1/tasks" } },
      { request: { mode: "no-cors", method: "GET", url: "https://lifeos.example/_next/static/app.js" } },
      { request: { mode: "navigate", method: "GET", url: "https://other.example/" } },
      { request: { mode: "navigate", method: "POST", url: "https://lifeos.example/login" } },
    ]) {
      expect(request(event).response).toBeUndefined();
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("serves a navigation from the network unchanged, preloaded or fetched", async () => {
    const page = new Response("<html>nonce</html>", { headers: { "Cache-Control": "no-store" } });
    const { request } = load({ network: async () => page });
    const preloaded = new Response("preloaded");
    const first = request({
      request: { mode: "navigate", method: "GET", url: "https://lifeos.example/tasks" },
      preloadResponse: Promise.resolve(preloaded),
    });
    expect(await first.response).toBe(preloaded);
    const second = request({
      request: { mode: "navigate", method: "GET", url: "https://lifeos.example/tasks" },
      preloadResponse: Promise.resolve(undefined),
    });
    expect(await second.response).toBe(page);
  });

  it("shows a fixed offline page, without data or script, when the network fails", async () => {
    const { request } = load({
      network: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const { response } = request({
      request: { mode: "navigate", method: "GET", url: 'https://lifeos.example/search?q="><script>x</script>' },
      preloadResponse: Promise.reject(new TypeError("Failed to fetch")),
    });
    const offline = await (response as Promise<Response>);
    expect(offline.status).toBe(503);
    expect(offline.headers.get("cache-control")).toBe("no-store");
    expect(offline.headers.get("content-security-policy")).toContain("default-src 'none'");
    const html = await offline.text();
    expect(html).toContain('<html lang="fr">');
    expect(html).toContain("<title>Hors connexion · LifeOS</title>");
    expect(html).toContain("<h1>LifeOS n’est pas joignable</h1>");
    expect(html).not.toMatch(/<script/i);
    expect(html).toContain('href="/search?q=%22%3E%3Cscript%3Ex%3C/script%3E"');
  });

  it("keeps shared files in memory and hands them over once", async () => {
    const { request, listeners } = load();
    const form = new FormData();
    form.append("file", new File(["ticket"], "ticket.jpg", { type: "image/jpeg" }));
    form.append("file", "not a file");
    const share = request({
      request: {
        mode: "navigate",
        method: "POST",
        url: "https://lifeos.example/partage",
        formData: async () => form,
      },
    });
    const redirect = await (share.response as Promise<Response>);
    expect(redirect.status).toBe(303);
    expect(redirect.headers.get("location")).toBe("https://lifeos.example/documents?partage=1");

    const ask = async () => {
      const replies: { type: string; files: File[] }[] = [];
      const kept: Promise<unknown>[] = [];
      listeners.get("message")?.({
        data: { type: "lifeos-share-request" },
        source: { postMessage: (message: { type: string; files: File[] }) => replies.push(message) },
        waitUntil: (p: Promise<unknown>) => kept.push(p),
      });
      await Promise.all(kept);
      return replies;
    };
    const first = await ask();
    expect(first).toHaveLength(1);
    expect(first[0]?.type).toBe("lifeos-share");
    expect(first[0]?.files.map((f) => f.name)).toEqual(["ticket.jpg"]);
    // The worker is released once the page has the files.
    await Promise.all(share.kept);
    expect((await ask())[0]?.files).toEqual([]);
  });

  it("forgets shared files nobody asked for after two minutes", async () => {
    vi.useFakeTimers();
    try {
      const { request, listeners } = load();
      const form = new FormData();
      form.append("file", new File(["x"], "scan.pdf", { type: "application/pdf" }));
      const share = request({
        request: { mode: "navigate", method: "POST", url: "https://lifeos.example/partage", formData: async () => form },
      });
      await vi.advanceTimersByTimeAsync(120_000);
      await Promise.all(share.kept);
      const replies: { files: File[] }[] = [];
      const kept: Promise<unknown>[] = [];
      listeners.get("message")?.({
        data: { type: "lifeos-share-request" },
        source: { postMessage: (message: { files: File[] }) => replies.push(message) },
        waitUntil: (p: Promise<unknown>) => kept.push(p),
      });
      await Promise.all(kept);
      expect(replies[0]?.files).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
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
