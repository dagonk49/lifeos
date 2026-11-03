import { api } from "@/lib/api";
import type { PushConfig, PushDevice } from "@/lib/types";

/**
 * Web Push on this browser (ADR 0019): the service worker at /sw.js shows what the server
 * pushes. Permission is asked only from a click of the owner (browsers refuse it otherwise,
 * and a prompt on page load would be an annoyance).
 */

export const SERVICE_WORKER_URL = "/sw.js";

interface PushEnvironment {
  navigator: Navigator;
  window: Window;
}

function environment(): PushEnvironment | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return null;
  return { navigator, window };
}

/** Service worker, Push API and notifications are all there (and the page is secure). */
export function isPushSupported(env: PushEnvironment | null = environment()): boolean {
  if (!env) return false;
  return (
    env.window.isSecureContext !== false &&
    "serviceWorker" in env.navigator &&
    "PushManager" in env.window &&
    "Notification" in env.window
  );
}

/** iPhone or iPad (iPadOS reports itself as a Mac with a touch screen). */
export function isIos(userAgent: string, maxTouchPoints = 0): boolean {
  return /iPad|iPhone|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && maxTouchPoints > 1);
}

/** Opened from the home screen (installed app), not in a browser tab. */
export function isStandalone(env: PushEnvironment | null = environment()): boolean {
  if (!env) return false;
  const standalone = (env.navigator as Navigator & { standalone?: boolean }).standalone === true;
  const media = typeof env.window.matchMedia === "function" && env.window.matchMedia("(display-mode: standalone)").matches;
  return standalone || media;
}

/** iOS delivers Web Push only to LifeOS added to the home screen (iOS 16.4+). */
export function needsHomeScreen(env: PushEnvironment | null = environment()): boolean {
  if (!env) return false;
  return isIos(env.navigator.userAgent, env.navigator.maxTouchPoints) && !isStandalone(env);
}

/** The VAPID public key (base64url) as the bytes PushManager.subscribe expects. */
export function urlBase64ToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]*$/.test(value)) throw new Error("Clé de notification invalide.");
  const base64 = (value + "=".repeat((4 - (value.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function sameKey(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return false;
  const left = new Uint8Array(a);
  return left.length === b.length && left.every((byte, i) => byte === b[i]);
}

/** A readable name for this device, e.g. "Android · Chrome" (the owner can rename it). */
export function deviceLabel(userAgent: string, maxTouchPoints = 0): string {
  const system = isIos(userAgent, maxTouchPoints)
    ? /iPad/.test(userAgent) || /Macintosh/.test(userAgent)
      ? "iPad"
      : "iPhone"
    : /Android/.test(userAgent)
      ? "Android"
      : /Windows/.test(userAgent)
        ? "Windows"
        : /Mac OS X|Macintosh/.test(userAgent)
          ? "Mac"
          : /Linux/.test(userAgent)
            ? "Linux"
            : "Appareil";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Firefox\//.test(userAgent)
      ? "Firefox"
      : /Chrome\/|CriOS\//.test(userAgent)
        ? "Chrome"
        : /Safari\//.test(userAgent)
          ? "Safari"
          : null;
  return browser ? `${system} · ${browser}` : system;
}

export class PushPermissionError extends Error {
  constructor() {
    super(
      "Les notifications sont bloquées pour LifeOS dans ce navigateur : autorisez-les dans les réglages du site, puis réessayez.",
    );
    this.name = "PushPermissionError";
  }
}

async function registration(): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" });
  return navigator.serviceWorker.ready;
}

/** This browser's subscription, if the worker is installed and subscribed. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const existing = await navigator.serviceWorker.getRegistration("/");
  return existing ? existing.pushManager.getSubscription() : null;
}

/**
 * Asks the permission (call it from a click), subscribes with the server's key and
 * registers the device. A subscription made with another key (rotated) is replaced.
 */
export async function enableOnThisDevice(): Promise<PushDevice> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new PushPermissionError();
  const config = await api<PushConfig>("/v1/notifications/push/config");
  const key = urlBase64ToUint8Array(config.public_key);
  const worker = await registration();
  let subscription = await worker.pushManager.getSubscription();
  if (subscription && !sameKey(subscription.options?.applicationServerKey, key)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await worker.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  const json = subscription.toJSON();
  return api<PushDevice>("/v1/notifications/devices", {
    method: "POST",
    body: {
      endpoint: json.endpoint,
      keys: json.keys,
      label: deviceLabel(navigator.userAgent, navigator.maxTouchPoints),
    },
    idempotent: true,
  });
}

/** Unsubscribes this browser and revokes its device on the server. */
export async function disableOnThisDevice(deviceId: string | null): Promise<void> {
  const subscription = await currentSubscription();
  await subscription?.unsubscribe();
  if (deviceId) await api(`/v1/notifications/devices/${deviceId}`, { method: "DELETE" });
}
