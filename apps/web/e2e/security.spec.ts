import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect, test } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");

/**
 * Every page of the client: public ones, those of a signed-in owner (the HTML is the same
 * without a session, the API checks it) and the 404 page.
 */
const LOGIN = { path: "/login", status: 200 };
const PAGES: { path: string; status: number }[] = [
  LOGIN,
  { path: "/enroll", status: 200 },
  ...["/", "/chat", "/tasks", "/agenda", "/journal", "/goals", "/documents", "/finances", "/reviews"]
    .concat(["/memory", "/search", "/approvals", "/settings", "/connexions/retour"])
    .map((path) => ({ path, status: 200 })),
  { path: "/page-inexistante", status: 404 },
];

/** The main navigation (AppShell), with the page each link opens. */
const NAV: [label: string, path: string][] = [
  ["Accueil", "/"],
  ["Assistant", "/chat"],
  ["Tâches", "/tasks"],
  ["Journal", "/journal"],
  ["Objectifs", "/goals"],
  ["Documents", "/documents"],
  ["Finances", "/finances"],
  ["Bilans", "/reviews"],
  ["Mémoire", "/memory"],
  ["Recherche", "/search"],
  ["Approbations", "/approvals"],
  ["Réglages", "/settings"],
];

/** Injected into the login page: none of it may run. */
const INJECTED = [
  `<script>window.__lifeosInjected = "inline"</script>`,
  `<script nonce="AAAAAAAAAAAAAAAAAAAAAA==">window.__lifeosInjected = "guessed nonce"</script>`,
  // Same origin, yet without a nonce: 'strict-dynamic' ignores 'self'.
  `<script src="/injected.js"></script>`,
  `<img alt="" src="data:," onerror="window.__lifeosInjected = 'event handler'">`,
].join("");

function directive(policy: string, name: string): string[] {
  const found = policy
    .split(";")
    .map((part) => part.trim().split(/\s+/))
    .find(([key]) => key === name);
  return found?.slice(1) ?? [];
}

/** A fresh enrollment link for the owner (other journeys used the previous ones). */
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

function navigation(page: Page) {
  return page.getByRole("navigation", { name: "Navigation principale" });
}

test("every page gets its own nonce policy, carried by all of its scripts", async ({ request }) => {
  const nonces = new Set<string>();
  // /login twice: a new nonce for every response, not one per page.
  for (const { path, status } of [...PAGES, LOGIN]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(status);
    const policy = response.headers()["content-security-policy"] ?? "";
    const nonce = /'nonce-([A-Za-z0-9+/]{22}==)'/.exec(policy)?.[1] ?? "";
    expect(nonce, `${path}: nonce in ${policy}`).not.toBe("");
    // Production build: neither inline scripts nor eval.
    expect(directive(policy, "script-src"), path).toEqual(["'self'", `'nonce-${nonce}'`, "'strict-dynamic'"]);
    expect(directive(policy, "style-src"), path).toEqual(["'self'", "'unsafe-inline'"]);
    expect(directive(policy, "connect-src"), path).toEqual(["'self'"]);
    // The nonce is only worth something if no cache ever serves the page again.
    expect(response.headers()["cache-control"], path).toContain("no-store");
    const scripts = (await response.text()).match(/<script\b[^>]*>/g) ?? [];
    expect(scripts.length, path).toBeGreaterThan(0);
    for (const script of scripts) expect(script, path).toContain(` nonce="${nonce}"`);
    nonces.add(nonce);
  }
  expect(nonces.size).toBe(PAGES.length + 1);

  // A policy or a nonce sent by the client is never used for rendering.
  const forged = await request.get("/login", {
    headers: { "Content-Security-Policy": "script-src 'nonce-forged'", "x-nonce": "forged" },
  });
  expect(forged.headers()["content-security-policy"]).not.toContain("forged");
  expect(await forged.text()).not.toContain("forged");
});

test("a script injected without the nonce is blocked, the page still works", async ({ page, cspViolations }) => {
  // The login page as an attacker would have altered it, policy unchanged.
  await page.route(
    (url) => url.pathname === "/login",
    async (route) => {
      if (route.request().resourceType() !== "document") return route.continue();
      const response = await route.fetch();
      const html = await response.text();
      expect(html).toContain("</body>");
      const headers = { ...response.headers() };
      // The body changes: the browser counts it again, uncompressed.
      delete headers["content-length"];
      delete headers["content-encoding"];
      await route.fulfill({
        status: response.status(),
        headers,
        body: html.replace("</body>", `${INJECTED}</body>`),
      });
    },
  );
  await page.goto("/login");
  // The framework scripts, which carry the nonce, ran: the page is hydrated.
  await expect.poll(() => page.evaluate(() => "next" in window)).toBe(true);
  await expect(page.getByRole("button", { name: "Se connecter avec une passkey" })).toBeEnabled();

  // Markup inserted later (DOM injection) cannot run an event handler either.
  await page.evaluate(() =>
    document.body.insertAdjacentHTML("beforeend", `<img alt="" src="data:," onerror="window.__lifeosInjected = 'later'">`),
  );

  const blocked = () =>
    cspViolations
      .map(({ directive, blocked }) => `${directive} ${blocked.startsWith("http") ? new URL(blocked).pathname : blocked}`)
      .sort();
  await expect
    .poll(blocked)
    .toEqual([
      "script-src-attr inline",
      "script-src-attr inline",
      "script-src-elem /injected.js",
      "script-src-elem inline",
      "script-src-elem inline",
    ]);
  expect(await page.evaluate(() => (window as { __lifeosInjected?: string }).__lifeosInjected)).toBeUndefined();
  expect(cspViolations.every((violation) => violation.page === "/login")).toBe(true);
  cspViolations.splice(0); // expected ones, checked above
});

test("passkeys, navigation and assistant work under the policy, without any violation", async ({ page }) => {
  test.setTimeout(120_000);
  await addVirtualAuthenticator(page);

  // Enrolment with a passkey.
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E sécurité");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  // Every page, through the router then fully reloaded (server rendering and hydration).
  for (const [label, path] of NAV) {
    await navigation(page).getByRole("link", { name: label }).click();
    await expect(page).toHaveURL((url) => url.pathname === path);
    await page.reload();
    await expect(navigation(page).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  }

  // The assistant answers (fake provider) once the AI is allowed for the personal space
  // (settings, where the loop ended), which an earlier journey may already have done.
  const personal = page.getByRole("listitem").filter({ hasText: "Personnel" }).filter({ hasText: "IA" });
  const allow = personal.getByRole("button", { name: "Autoriser l'IA" });
  await expect(allow.or(personal.getByText("IA autorisée"))).toBeVisible();
  if (await allow.isVisible()) await allow.click();
  await expect(personal.getByText("IA autorisée")).toBeVisible();
  await navigation(page).getByRole("link", { name: "Assistant" }).click();
  const newConversation = page.getByRole("button", { name: "Nouvelle conversation", exact: true });
  const created = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/chat/conversations" && response.request().method() === "POST",
  );
  await newConversation.click();
  await created;
  await expect(newConversation).toBeEnabled();
  await page.getByLabel("Votre message").fill("Vérifie la politique de sécurité");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(
    page.getByRole("list", { name: "Messages" }).getByText("[fake] Vérifie la politique de sécurité"),
  ).toBeVisible();

  // Sign out, then sign in again with the passkey.
  await page.getByRole("button", { name: "Déconnexion" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("button", { name: "Se connecter avec une passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  // The 404 page, rendered per request as well.
  const missing = await page.goto("/page-inexistante");
  expect(missing?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "404" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => "next" in window)).toBe(true);
});

const PHONE_UA =
  "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36";

test("a lost phone's session is signed out from the computer and goes back to the login page", async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000);
  // The phone: another browser, its own passkey and session.
  const phoneContext = await browser.newContext({ baseURL: test.info().project.use.baseURL, userAgent: PHONE_UA });
  try {
    const phone = await phoneContext.newPage();
    await addVirtualAuthenticator(phone);
    await phone.goto(`/enroll#token=${enrollmentToken()}`);
    await phone.getByLabel("Nom de l'appareil").fill("Téléphone E2E sécurité");
    await phone.getByRole("button", { name: "Créer ma passkey" }).click();
    await expect(phone.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

    // The computer, signed in just now: a recent login, no passkey confirmation needed.
    await addVirtualAuthenticator(page);
    await page.goto(`/enroll#token=${enrollmentToken()}`);
    await page.getByLabel("Nom de l'appareil").fill("Ordinateur E2E sécurité");
    await page.getByRole("button", { name: "Créer ma passkey" }).click();
    await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();
    await navigation(page).getByRole("link", { name: "Réglages" }).click();
    await expect(page.getByRole("button", { name: `Révoquer la session ${PHONE_UA}` })).toBeVisible();

    // Earlier journeys may have left sessions open: all of them go, this one stays.
    await page.getByRole("button", { name: "Déconnecter les autres sessions" }).click();
    await expect(page.getByText(/^\d+ autres? sessions? (a|ont) été déconnectées?\.$/)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Révoquer la session/ })).toHaveCount(0);
    await expect(page.getByText("Cette session")).toBeVisible();

    // The phone's next request is refused: back to the login page, nothing shown.
    await phone.reload();
    await expect(phone).toHaveURL(/\/login$/);
    // The owner signs in again on the phone with its passkey (the allowed journey).
    await phone.getByRole("button", { name: "Se connecter avec une passkey" }).click();
    await expect(phone.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

    // The computer's own session was kept.
    await page.reload();
    await expect(navigation(page).getByRole("link", { name: "Réglages" })).toHaveAttribute("aria-current", "page");
  } finally {
    await phoneContext.close();
  }
});
