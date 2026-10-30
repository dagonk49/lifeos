import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect, test } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");

/** A fresh enrollment link for the owner (the MVP journey used the first one). */
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

async function nav(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name }).click();
}

function todayAtNoon(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T12:00`;
}

test("Task 23 journey: home cards hidden and kept, week view with today's task", async ({ page }) => {
  test.setTimeout(90_000);
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E accueil");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  // A task due today at noon: in the current week whatever the week start.
  await nav(page, "Tâches");
  await page.getByLabel("Intitulé").fill("Préparer la semaine E2E");
  await page.getByLabel("Échéance").fill(todayAtNoon());
  await page.getByRole("button", { name: "Ajouter" }).click();
  await expect(page.getByRole("checkbox", { name: "Terminer « Préparer la semaine E2E »" })).toBeVisible();
  await nav(page, "Accueil");
  await expect(page).toHaveURL(/\/$/);

  // Hide a card: kept on the server, so still hidden after a reload.
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Personnaliser" }).click();
  await page.getByRole("checkbox", { name: "Afficher « Mémoire »" }).uncheck();
  await page.getByRole("button", { name: "Monter « Suggestions »" }).click();
  await page.getByRole("button", { name: "Enregistrer la disposition" }).click();
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Suggestions", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toHaveCount(0);

  // The week view, kept as the default view, shows the task on today's column.
  await page.getByRole("button", { name: "Semaine", exact: true }).click();
  const today = page.getByRole("region").filter({ has: page.getByText("· aujourd'hui") });
  await expect(today.getByRole("link", { name: "Préparer la semaine E2E" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Semaine", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(today.getByRole("link", { name: "Préparer la semaine E2E" })).toBeVisible();
  await expect(today.getByText(/^Échéance \d\d:\d\d$/).first()).toBeVisible();

  // Back to the defaults for the journeys that follow.
  await page.getByRole("button", { name: "Jour", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agenda du jour" })).toBeVisible();
  await page.getByRole("button", { name: "Personnaliser" }).click();
  await page.getByRole("button", { name: "Rétablir l'ordre par défaut" }).click();
  await page.getByRole("button", { name: "Enregistrer la disposition" }).click();
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toBeVisible();
});
