import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";

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

test("Step 3 journey: goals, reviews, memory review, agents, consolidation, semantic search", async ({ page }) => {
  test.setTimeout(120_000);
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E étape 3");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  // Goals (F06): a measurable goal, a check-in, progress computed locally.
  await nav(page, "Objectifs");
  await page.getByRole("button", { name: "Nouvel objectif" }).click();
  await page.getByLabel("Intitulé").fill("Courir 10 km");
  await page.getByLabel("Mesurable (valeur cible)").check();
  await page.getByLabel("Unité", { exact: true }).fill("km");
  await page.getByLabel("Valeur de départ", { exact: true }).fill("0");
  await page.getByLabel("Valeur cible", { exact: true }).fill("10");
  await page.getByRole("button", { name: "Créer l'objectif" }).click();
  await page.getByLabel("Valeur actuelle (km)").fill("5");
  await page.getByRole("button", { name: "Enregistrer le point" }).click();
  await expect(page.getByRole("progressbar").first()).toHaveAttribute("aria-valuenow", "50");

  // Reviews (F05): facts computed locally, separated from the assistant's reading.
  await nav(page, "Bilans");
  await page.getByRole("button", { name: "Établir le bilan" }).click();
  await expect(page.getByRole("region", { name: "Faits (calculés localement, sans IA)" })).toBeVisible();

  // Memory quality (F07): an exact duplicate is flagged, the owner keeps both.
  await nav(page, "Mémoire");
  for (const statement of ["J'aime le thé vert.", "j'aime le the vert"]) {
    await page.getByLabel("Énoncé").fill(statement);
    await page.getByRole("button", { name: "Ajouter" }).click();
    // Saved: the field is emptied and the fact is listed.
    await expect(page.getByLabel("Énoncé")).toHaveValue("");
    await expect(page.getByRole("listitem").filter({ hasText: statement }).first()).toBeVisible();
  }
  await page.getByRole("tab", { name: /À vérifier/ }).click();
  await expect(page.getByText("Même texte enregistré plusieurs fois")).toBeVisible();
  await page.getByRole("button", { name: "Garder les deux" }).click();
  await expect(page.getByText("Rien à vérifier.")).toBeVisible();

  // Specialised agent (F08): explicit choice, access granted on demand, then the reply.
  await nav(page, "Assistant");
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await page.getByLabel("Agent").selectOption({ label: "Planification" });
  await page.getByLabel("Votre message").fill("Organise ma semaine");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(page.getByText("Autoriser « Planification » dans cet espace ?")).toBeVisible();
  await page.getByRole("button", { name: "Autoriser", exact: true }).click();
  await page.getByRole("button", { name: "Renvoyer" }).click();
  const messages = page.getByRole("list", { name: "Messages" });
  await expect(messages.getByText("[fake] Organise ma semaine")).toBeVisible();
  await expect(messages.getByText("Agent : Planification")).toBeVisible();

  // Consolidation: the (fake) model finds nothing durable; nothing is saved.
  await page.getByRole("button", { name: "Retenir l'essentiel" }).click();
  await expect(page.getByText("Rien de durable à retenir dans les nouveaux messages.")).toBeVisible();

  // Search: goals are searchable and link to their page; the local semantic index is on.
  await nav(page, "Recherche");
  await page.getByLabel("Rechercher").fill("courir");
  await page.getByRole("button", { name: "Rechercher" }).click();
  await expect(page.getByText(/Index sémantique : \d+ \/ \d+ éléments/)).toBeVisible();
  const goalHit = page.getByRole("listitem").filter({ hasText: "Courir 10 km" });
  await expect(goalHit.getByText("Objectif", { exact: true })).toBeVisible();
  await goalHit.getByRole("link", { name: "Ouvrir" }).click();
  await expect(page).toHaveURL(/\/goals\?id=/);
  await expect(page.getByRole("progressbar").first()).toHaveAttribute("aria-valuenow", "50");
});
