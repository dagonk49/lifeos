import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deviceLabel,
  disableOnThisDevice,
  enableOnThisDevice,
  isIos,
  isPushSupported,
  isStandalone,
  needsHomeScreen,
  PushPermissionError,
  urlBase64ToUint8Array,
} from "./push";

const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";
const IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15";
const ANDROID =
  "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36";
const FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0";

function env(options: { ua?: string; touch?: number; standalone?: boolean; media?: boolean; push?: boolean }) {
  const navigator = {
    userAgent: options.ua ?? ANDROID,
    maxTouchPoints: options.touch ?? 0,
    standalone: options.standalone,
    ...(options.push === false ? {} : { serviceWorker: {} }),
  } as unknown as Navigator;
  const window = {
    isSecureContext: true,
    matchMedia: () => ({ matches: options.media ?? false }),
    ...(options.push === false ? {} : { PushManager: class {}, Notification: class {} }),
  } as unknown as Window;
  return { navigator, window };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("urlBase64ToUint8Array", () => {
  it("decodes a base64url VAPID key without padding", () => {
    // 65 bytes: an uncompressed P-256 point starts with 0x04.
    const key = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4";
    const bytes = urlBase64ToUint8Array(key);
    expect(bytes).toHaveLength(65);
    expect(bytes[0]).toBe(4);
    expect(Array.from(urlBase64ToUint8Array("-_8"))).toEqual([0xfb, 0xff]);
  });

  it("refuses characters outside base64url", () => {
    expect(() => urlBase64ToUint8Array("ab+/")).toThrow();
  });
});

describe("support detection", () => {
  it("needs a service worker, the Push API and notifications", () => {
    expect(isPushSupported(env({}))).toBe(true);
    expect(isPushSupported(env({ push: false }))).toBe(false);
    expect(isPushSupported(null)).toBe(false);
  });

  it("asks iPhone and iPad users to add LifeOS to the home screen first", () => {
    expect(isIos(IPHONE)).toBe(true);
    expect(isIos(IPAD, 5)).toBe(true); // iPadOS reports a Mac with a touch screen
    expect(isIos(IPAD, 0)).toBe(false); // a real Mac
    expect(needsHomeScreen(env({ ua: IPHONE }))).toBe(true);
    expect(needsHomeScreen(env({ ua: IPHONE, standalone: true }))).toBe(false);
    expect(needsHomeScreen(env({ ua: IPAD, touch: 5, media: true }))).toBe(false);
    expect(needsHomeScreen(env({ ua: ANDROID }))).toBe(false);
    expect(isStandalone(env({ media: true }))).toBe(true);
  });

  it("names the device from its user agent", () => {
    expect(deviceLabel(ANDROID)).toBe("Android · Chrome");
    expect(deviceLabel(IPHONE)).toBe("iPhone · Safari");
    expect(deviceLabel(IPAD, 5)).toBe("iPad · Safari");
    expect(deviceLabel(FIREFOX)).toBe("Linux · Firefox");
    expect(deviceLabel("curl/8")).toBe("Appareil");
  });
});

describe("enableOnThisDevice", () => {
  const KEY = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4";

  function stubBrowser(permission: NotificationPermission, existing: unknown = null) {
    const subscription = {
      toJSON: () => ({ endpoint: "https://fcm.googleapis.com/fcm/send/x", keys: { p256dh: "p", auth: "a" } }),
      unsubscribe: vi.fn(async () => true),
      options: { applicationServerKey: urlBase64ToUint8Array(KEY).buffer },
    };
    const pushManager = {
      getSubscription: vi.fn(async () => existing),
      subscribe: vi.fn(async () => subscription),
    };
    const registration = { pushManager };
    vi.stubGlobal("Notification", { requestPermission: vi.fn(async () => permission) });
    vi.stubGlobal("PushManager", class {});
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("navigator", {
      userAgent: ANDROID,
      maxTouchPoints: 0,
      serviceWorker: {
        register: vi.fn(async () => registration),
        ready: Promise.resolve(registration),
        getRegistration: vi.fn(async () => registration),
      },
    });
    const calls: { url: string; method: string; body: unknown; headers: Record<string, string> }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({
          url,
          method: init?.method ?? "GET",
          body: init?.body ? JSON.parse(String(init.body)) : null,
          headers: (init?.headers ?? {}) as Record<string, string>,
        });
        if (url.endsWith("/push/config")) return new Response(JSON.stringify({ enabled: true, public_key: KEY }));
        if (init?.method === "DELETE") return new Response(null, { status: 204 });
        return new Response(JSON.stringify({ id: "d1", label: "Android · Chrome", current: true }), { status: 201 });
      }),
    );
    return { calls, pushManager, subscription };
  }

  it("subscribes with the server key and registers the device", async () => {
    const { calls, pushManager } = stubBrowser("granted");
    const device = await enableOnThisDevice();
    expect(device.id).toBe("d1");
    const [{ applicationServerKey, userVisibleOnly }] = pushManager.subscribe.mock.calls[0] as unknown as [
      { applicationServerKey: Uint8Array; userVisibleOnly: boolean },
    ];
    expect(userVisibleOnly).toBe(true);
    expect(Array.from(applicationServerKey)).toEqual(Array.from(urlBase64ToUint8Array(KEY)));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/v1/notifications/devices");
    expect(post?.body).toEqual({
      endpoint: "https://fcm.googleapis.com/fcm/send/x",
      keys: { p256dh: "p", auth: "a" },
      label: "Android · Chrome",
    });
    expect(post?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("stops when the permission is refused, without subscribing", async () => {
    const { calls, pushManager } = stubBrowser("denied");
    await expect(enableOnThisDevice()).rejects.toBeInstanceOf(PushPermissionError);
    expect(pushManager.subscribe).not.toHaveBeenCalled();
    expect(calls).toEqual([]);
  });

  it("replaces a subscription made with another key", async () => {
    const stale = { unsubscribe: vi.fn(async () => true), options: { applicationServerKey: new Uint8Array([1, 2]).buffer } };
    const { pushManager } = stubBrowser("granted", stale);
    await enableOnThisDevice();
    expect(stale.unsubscribe).toHaveBeenCalled();
    expect(pushManager.subscribe).toHaveBeenCalled();
  });

  it("disables: unsubscribes and revokes the device", async () => {
    const unsubscribe = vi.fn(async () => true);
    const { calls } = stubBrowser("granted", { unsubscribe });
    await disableOnThisDevice("d1");
    expect(unsubscribe).toHaveBeenCalled();
    expect(calls.map((c) => [c.method, c.url])).toEqual([["DELETE", "/v1/notifications/devices/d1"]]);
  });
});
