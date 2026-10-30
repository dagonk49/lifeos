import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect, test } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");

/** A fresh enrollment link for the owner (earlier journeys used the previous ones). */
function enrollmentToken(): string {
  const output = execFileSync(
    "uv",
    ["run", "--directory", backend, "lifeos-admin", "enrollment-token", "--email", "e2e@example.org"],
    { env: { ...process.env, ...backendEnv }, encoding: "utf8" },
  );
  const token = /enroll#token=(\S+)/.exec(output)?.[1];
  if (!token) throw new Error(`no enrollment token in CLI output:\n${output}`);
  return token;
}

async function addVirtualAuthenticator(page: Page): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
}

test("the service worker and the manifest are served within the policy, never cached", async ({ request }) => {
  const worker = await request.get("/sw.js");
  expect(worker.status()).toBe(200);
  expect(worker.headers()["content-type"]).toContain("text/javascript");
  expect(worker.headers()["cache-control"]).toBe("no-cache");
  // Never a cache nor a database: pages carry a per-request nonce, data stays on the server.
  expect(await worker.text()).not.toMatch(/caches|indexedDB|importScripts/);
  const manifest = await request.get("/manifest.webmanifest");
  expect(manifest.headers()["content-type"]).toContain("application/manifest+json");
  const body = (await manifest.json()) as { display: string; icons: { src: string }[] };
  expect(body.display).toBe("standalone");
  for (const icon of body.icons) {
    const image = await request.get(icon.src);
    expect(image.status(), icon.src).toBe(200);
    expect(image.headers()["content-type"]).toBe("image/png");
  }
  const page = await request.get("/login");
  const policy = page.headers()["content-security-policy"] ?? "";
  expect(policy).toContain("worker-src 'self'");
  expect(policy).toContain("manifest-src 'self'");
});

test("Push journey: enable on this device, see it listed, send a test, revoke it", async ({ page, context }) => {
  await context.grantPermissions(["notifications"]);
  // No push service in the test browser: the subscription is a stub (endpoint of a known
  // push service, valid P-256 key, 16-byte secret); the backend's fake sender sends nothing.
  await page.addInitScript(() => {
    const subscription = {
      endpoint: "https://fcm.googleapis.com/fcm/send/e2e-device",
      options: { applicationServerKey: null },
      toJSON: () => ({
        endpoint: "https://fcm.googleapis.com/fcm/send/e2e-device",
        keys: {
          p256dh: "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
          auth: "BTBZMqHH6r4Tts7J_aSIgg",
        },
      }),
      unsubscribe: async () => {
        active = null;
        return true;
      },
    };
    let active: typeof subscription | null = null;
    PushManager.prototype.subscribe = async function () {
      active = subscription;
      return subscription as unknown as PushSubscription;
    };
    PushManager.prototype.getSubscription = async function () {
      return active as unknown as PushSubscription | null;
    };
  });
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E push");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Réglages" }).click();
  const section = page.locator("#notifications-telephone");
  await section.getByRole("button", { name: "Activer sur cet appareil" }).click();
  await expect(section.getByText("Notifications activées sur cet appareil.")).toBeVisible();
  const device = section.getByRole("listitem").filter({ hasText: "Cet appareil" });
  await expect(device).toBeVisible();
  await expect(section.getByRole("button", { name: "Désactiver sur cet appareil" })).toBeVisible();
  // The worker is installed from /sw.js, within the Content Security Policy.
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistration("/"))?.active?.scriptURL)).toMatch(
    /\/sw\.js$/,
  );

  await device.getByRole("button", { name: /^Envoyer un test à/ }).click();
  await expect(section.getByText(/Notification de test envoyée à .* elle arrive dans la minute\./)).toBeVisible();

  await device.getByRole("button", { name: /^Révoquer/ }).click();
  await expect(section.getByText(/ne recevra plus de notifications\./)).toBeVisible();
  await expect(section.getByText("Aucun appareil pour l'instant.")).toBeVisible();
  await expect(section.getByRole("button", { name: "Activer sur cet appareil" })).toBeVisible();
});
