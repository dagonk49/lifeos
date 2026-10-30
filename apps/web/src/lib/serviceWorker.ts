/**
 * Source of the LifeOS service worker, served at /sw.js by src/app/sw.js/route.ts
 * (ADR 0019, ADR 0022), registered on every page by components/ServiceWorkerRegistration.
 *
 * It never stores anything: no Cache Storage, no IndexedDB. Pages carry a per-request CSP
 * nonce and are served no-store; a cached page would replay an old nonce, and personal data
 * would stay on the phone. What it does:
 *  - push: shows the payload as is (title, text, path), without calling the API, so it works
 *    when the server is only reachable over a VPN; taps open same-origin paths only. When
 *    the browser replaces its subscription, the new one is sent to the API for the device it
 *    replaces; it never enables push on its own.
 *  - navigations (GET, this origin): passed to the network unchanged (navigation preload:
 *    one request, with the browser's own cookies and headers). Only when the network fails,
 *    a fixed page built here, holding no data, says that LifeOS is unreachable.
 *  - share target (POST /partage, see the manifest): the shared files stay in this worker's
 *    memory until the documents page asks for them (2 minutes at most), then are handed over
 *    and forgotten. Nothing is uploaded before the owner sends it from that page.
 * Every other request (scripts, styles, images, API calls) never goes through it.
 */
export const SERVICE_WORKER_SOURCE = String.raw`"use strict";

const ORIGIN = self.location.origin;
const SHARE_ACTION = "/partage";
const SHARE_PAGE = "/documents?partage=1";
const SHARE_MAX_FILES = 10;
const SHARE_WAIT_MS = 120000;

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

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
}

const OFFLINE_POLICY = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "img-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

// No script and no data: the same page for every path, with a link back to it.
function offlinePage(path) {
  const html =
    '<!doctype html><html lang="fr"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<meta name="color-scheme" content="light dark">' +
    "<title>Hors connexion · LifeOS</title><style>" +
    "body{margin:0;font-family:system-ui,sans-serif;background:#fafaf9;color:#1c1917;line-height:1.5}" +
    "main{max-width:32rem;margin:0 auto;padding:3rem 1rem}" +
    "h1{font-size:1.5rem;line-height:1.25;margin:0 0 1rem}" +
    "p{margin:0 0 1rem}.muted{color:#57534e;font-size:.875rem}" +
    "a{display:inline-flex;align-items:center;min-height:44px;padding:0 1rem;border-radius:.375rem;" +
    "background:#0f766e;color:#fff;font-weight:600;text-decoration:none}" +
    "a:focus-visible{outline:2px solid #0f766e;outline-offset:2px}" +
    "@media (prefers-color-scheme:dark){body{background:#0c0a09;color:#f5f5f4}.muted{color:#a8a29e}" +
    "a:focus-visible{outline-color:#5eead4}}" +
    "</style></head><body><main>" +
    "<h1>LifeOS n’est pas joignable</h1>" +
    "<p>Cet appareil n’atteint pas le serveur LifeOS. Vérifiez la connexion Internet ou le VPN, puis réessayez.</p>" +
    '<p class="muted">Rien n’est conservé sur cet appareil : vos données restent sur le serveur, ' +
    "et cette page n’en contient aucune.</p>" +
    '<p><a href="' +
    escapeHtml(path) +
    '">Réessayer</a></p></main></body></html>';
  return new Response(html, {
    status: 503,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": OFFLINE_POLICY,
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

async function navigate(event) {
  try {
    const preloaded = await event.preloadResponse;
    if (preloaded) return preloaded;
    return await fetch(event.request);
  } catch (error) {
    return offlinePage(safePath(event.request.url));
  }
}

// The files of the last share, in memory only: { files: Promise<File[]>, settle }.
let pendingShare = null;

function receiveShare(event) {
  const files = event.request
    .formData()
    .then((form) => form.getAll("file").filter((value) => typeof value !== "string").slice(0, SHARE_MAX_FILES))
    .catch(() => []);
  let settle = () => {};
  const handed = new Promise((resolve) => {
    settle = resolve;
  });
  const share = { files, settle };
  pendingShare = share;
  // Keeps the worker (and the files) alive until the page takes them, 2 minutes at most.
  event.waitUntil(
    Promise.race([handed, new Promise((resolve) => setTimeout(resolve, SHARE_WAIT_MS))]).then(() => {
      if (pendingShare === share) pendingShare = null;
    }),
  );
  return Response.redirect(new URL(SHARE_PAGE, ORIGIN).href, 303);
}

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  const preload = self.registration.navigationPreload;
  event.waitUntil(Promise.all([self.clients.claim(), preload ? preload.enable() : null]));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  // Scripts, styles, images and API calls never go through this worker.
  if (request.mode !== "navigate") return;
  const url = new URL(request.url);
  if (url.origin !== ORIGIN) return;
  if (request.method === "POST" && url.pathname === SHARE_ACTION) {
    event.respondWith(receiveShare(event));
  } else if (request.method === "GET") {
    event.respondWith(navigate(event));
  }
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "lifeos-share-request" || !event.source) return;
  const share = pendingShare;
  pendingShare = null;
  event.waitUntil(
    (async () => {
      const files = share ? await share.files : [];
      event.source.postMessage({ type: "lifeos-share", files });
      if (share) share.settle();
    })(),
  );
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
