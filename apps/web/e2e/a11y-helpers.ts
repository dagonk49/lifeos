import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");

/** Every page of the main navigation (AppShell NAV), with its document title. */
export const PAGES: { path: string; link: string; title: string }[] = [
  { path: "/", link: "Accueil", title: "Accueil · LifeOS" },
  { path: "/chat", link: "Assistant", title: "Assistant · LifeOS" },
  { path: "/tasks", link: "Tâches", title: "Tâches · LifeOS" },
  { path: "/agenda", link: "Agenda", title: "Agenda · LifeOS" },
  { path: "/mail", link: "E-mails", title: "E-mails · LifeOS" },
  { path: "/journal", link: "Journal", title: "Journal · LifeOS" },
  { path: "/goals", link: "Objectifs", title: "Objectifs · LifeOS" },
  { path: "/documents", link: "Documents", title: "Documents · LifeOS" },
  { path: "/finances", link: "Finances", title: "Finances · LifeOS" },
  { path: "/reviews", link: "Bilans", title: "Bilans · LifeOS" },
  { path: "/memory", link: "Mémoire", title: "Mémoire · LifeOS" },
  { path: "/search", link: "Recherche", title: "Recherche · LifeOS" },
  { path: "/approvals", link: "Approbations", title: "Approbations · LifeOS" },
  { path: "/settings", link: "Réglages", title: "Réglages · LifeOS" },
];

export function pageAt(path: string): (typeof PAGES)[number] {
  const found = PAGES.find((p) => p.path === path);
  if (!found) throw new Error(`no page ${path} in PAGES`);
  return found;
}

/** A fresh enrollment link for the owner (other journeys used the previous ones). */
export function enrollmentToken(): string {
  const output = execFileSync(
    "uv",
    ["run", "--directory", backend, "lifeos-admin", "enrollment-token", "--email", "e2e@example.org"],
    { env: { ...process.env, ...backendEnv }, encoding: "utf8" },
  );
  const token = /enroll#token=(\S+)/.exec(output)?.[1];
  if (!token) throw new Error(`no enrollment token in CLI output:\n${output}`);
  return token;
}

/** Chromium virtual authenticator: a platform passkey with user verification. */
export async function addVirtualAuthenticator(page: Page): Promise<void> {
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

/** Enrols a new passkey for this page and lands on the home page. */
export async function enrol(page: Page, device: string): Promise<void> {
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await expect(page).toHaveTitle("Enregistrer cet appareil · LifeOS");
  await page.getByLabel("Nom de l'appareil").fill(device);
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();
}

/**
 * Deletes the owner's tasks with this title, from the page (same-origin, signed in). The
 * specs share one database: a task left open would change what the other journeys see.
 */
export async function deleteTasksTitled(page: Page, title: string): Promise<void> {
  await page.evaluate(async (wanted) => {
    const response = await fetch("/v1/tasks?limit=200", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const { items } = (await response.json()) as { items: { id: string; title: string }[] };
    for (const task of items.filter((t) => t.title === wanted)) {
      await fetch(`/v1/tasks/${task.id}`, { method: "DELETE", headers: { Accept: "application/json" } });
    }
  }, title);
}

export async function nav(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name, exact: true }).click();
}

/** Opens a page from the navigation and waits for its heading and title. */
export async function openPage(page: Page, target: (typeof PAGES)[number]): Promise<void> {
  await nav(page, target.link);
  await expect(page).toHaveURL(new RegExp(`${target.path === "/" ? "/" : target.path}(\\?.*)?$`));
  await expect(page).toHaveTitle(target.title);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // Data-driven sections render after their first request.
  await expect(page.getByRole("status").filter({ hasText: /^Chargement/ })).toHaveCount(0);
}
