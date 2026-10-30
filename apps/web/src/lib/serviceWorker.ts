/**
 * Source of the push service worker, served at /sw.js by src/app/sw.js/route.ts (ADR 0019).
 *
 * Push only: it never caches nor intercepts requests. Pages carry a per-request CSP nonce
 * and are served no-store; a cached page would replay an old nonce. The payload is enough
 * on its own (title, text, path): the worker never calls the API to show a notification,
 * so it works when the server is only reachable over a VPN. Taps open same-origin paths
 * only. When the browser replaces its subscription, the new one is sent to the API for the
 * device it replaces; it never enables push on its own.
 */
export const SERVICE_WORKER_SOURCE = String.raw`"use strict";

const ORIGIN = self.location.origin;

function safePath(url) {
  if (typeof url !== "string" || url === "") return "/";
  try {
    const target = new URL(url, ORIGIN);
    if (target.origin !== ORIGIN) return "/";
    return target.pathname + target.search + target.hash;
  } catch (error) {
    return "/";
  }
}

function text(value, fallback, max) {
  return typeof value === "string" && value.trim() !== "" ? value.slice(0, max) : fallback;
}

function readPayload(data) {
  let value = {};
  try {
    value = data ? data.json() : {};
  } catch (error) {
    value = {};
  }
  if (value === null || typeof value !== "object") value = {};
  return {
    title: text(value.title, "LifeOS", 100),
    body: text(value.body, "Nouvelle notification", 300),
    tag: text(value.tag, "lifeos", 100),
    url: safePath(value.url),
  };
}

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  const message = readPayload(event.data);
  event.waitUntil(
    self.registration.showNotification(message.title, {
      body: message.body,
      tag: message.tag,
      icon: "/icons/icon-192.png",
      lang: "fr",
      data: { url: message.url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = new URL(safePath(data.url), ORIGIN).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (new URL(client.url).origin !== ORIGIN) continue;
        const focused = await client.focus();
        if (focused && typeof focused.navigate === "function") {
          try {
            await focused.navigate(target);
          } catch (error) {
            // Not controlled by this worker yet: it stays where it is.
          }
        }
        return;
      }
      await self.clients.openWindow(target);
    })(),
  );
});

self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const previous = event.oldSubscription;
      // Nothing to renew: enabling push is the owner's choice, in the settings.
      if (!previous) return;
      let fresh = event.newSubscription;
      if (!fresh) {
        if (!previous.options) return;
        fresh = await self.registration.pushManager.subscribe(previous.options);
      }
      const json = fresh.toJSON();
      await fetch("/v1/notifications/devices", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "Idempotency-Key": self.crypto.randomUUID(),
        },
        body: JSON.stringify({
          endpoint: json.endpoint,
          keys: json.keys,
          previous_endpoint: previous.endpoint,
        }),
      });
    })(),
  );
});
`;
