import { readFileSync } from "node:fs";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { TOKEN_FILE } from "./global-setup";

/** Chromium virtual authenticator: a platform passkey with user verification. */
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
  await page
    .getByRole("navigation", { name: "Navigation principale" })
    .getByRole("link", { name })
    .click();
}

function messages(page: Page) {
  return page.getByRole("list", { name: "Messages" });
}

test.describe.configure({ mode: "serial" });

test("MVP journey: enrol, organise, converse, remember, search, sign in again", async ({
  page,
}) => {
  await addVirtualAuthenticator(page);

  // Not signed in: redirected to the login page.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  // UC: first device enrolment from the server-issued link.
  const token = readFileSync(TOKEN_FILE, "utf8").trim();
  await page.goto(`/enroll#token=${token}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(
    page.getByRole("heading", { name: /^Aujourd'hui/ }),
  ).toBeVisible();
  await expect(
    page.getByText("Pas encore d'entrée aujourd'hui."),
  ).toBeVisible();

  // Tasks: create, then complete.
  await nav(page, "Tâches");
  await page.getByLabel("Intitulé").fill("Acheter du pain");
  await page.getByRole("button", { name: "Ajouter" }).click();
  const task = page.getByRole("checkbox", {
    name: "Terminer « Acheter du pain »",
  });
  await expect(task).toBeVisible();
  await task.click(); // controlled checkbox: the task leaves the open list
  await expect(page.getByText("Aucune tâche ouverte.")).toBeVisible();

  // Assistant without consent: degraded mode, the message is kept.
  await nav(page, "Assistant");
  await page.getByLabel("Votre message").fill("Bonjour LifeOS");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(page.getByText(/pas activé pour cet espace/)).toBeVisible();
  await expect(
    messages(page).getByText("Bonjour LifeOS", { exact: true }),
  ).toBeVisible();

  // Consent for the personal space (reinforced operation, recent passkey login).
  await nav(page, "Réglages");
  const personal = page
    .getByRole("listitem")
    .filter({ hasText: "Personnel" })
    .filter({ hasText: "IA" });
  await personal.getByRole("button", { name: "Autoriser l'IA" }).click();
  await expect(personal.getByText("IA autorisée")).toBeVisible();

  // Assistant with consent: a reply from the (fake) provider.
  await nav(page, "Assistant");
  await page
    .getByRole("button", { name: "Nouvelle conversation", exact: true })
    .click();
  await page
    .getByLabel("Votre message")
    .fill("Que dois-je faire aujourd'hui ?");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(
    messages(page).getByText("[fake] Que dois-je faire aujourd'hui ?"),
  ).toBeVisible();

  // Journal (UC01): write, then ask for proposals to review.
  await nav(page, "Journal");
  await page
    .getByLabel("Faits, accomplissements, difficultés…")
    .fill("Belle journée au jardin.");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText("Belle journée au jardin.")).toBeVisible();
  await page.getByRole("button", { name: "Extraire tâches et faits" }).click();
  await expect(
    page.getByText("L'assistant n'a rien trouvé à proposer."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approuver" }).click();
  await expect(page.getByText("Analysée")).toBeVisible();

  // Memory: declare, correct (history kept), export available.
  await nav(page, "Mémoire");
  await page.getByLabel("Énoncé").fill("Je préfère le thé");
  await page.getByRole("button", { name: "Ajouter" }).click();
  await expect(page.getByText("Je préfère le thé")).toBeVisible();
  await expect(page.getByText("Déclaré").first()).toBeVisible();
  await page.getByRole("button", { name: "Corriger" }).click();
  await page.getByLabel("Énoncé corrigé").fill("Je préfère le café");
  await page.getByRole("button", { name: "Enregistrer la correction" }).click();
  await expect(page.getByText("Je préfère le café")).toBeVisible();
  await page.getByRole("button", { name: "Historique" }).click();
  await expect(page.getByText("Je préfère le thé")).toBeVisible();
  // The export lives in Réglages > Mes données: the JSON, and the archive (JSON, CSV,
  // ICS, Markdown, README). Signed in just now: no passkey asked for.
  await page.getByRole("link", { name: "Exporter mes données" }).click();
  await expect(page).toHaveURL(/\/settings#mes-donnees$/);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "JSON seul" }).click();
  expect((await download).suggestedFilename()).toMatch(
    /^lifeos-export-\d{8}\.json$/,
  );
  const archive = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Télécharger l'archive (ZIP)" })
    .click();
  expect((await archive).suggestedFilename()).toMatch(
    /^lifeos-export-\d{8}\.zip$/,
  );

  // Search: French, accent-insensitive.
  await nav(page, "Recherche");
  await page.getByLabel("Rechercher").fill("cafe");
  await page.getByRole("button", { name: "Rechercher" }).click();
  await expect(page.getByText("Fait", { exact: true })).toBeVisible();
  await expect(page.locator("mark", { hasText: "café" })).toBeVisible();

  // Sign out, then sign in again with the passkey.
  await page.getByRole("button", { name: "Déconnexion" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page
    .getByRole("button", { name: "Se connecter avec une passkey" })
    .click();
  await expect(
    page.getByRole("heading", { name: /^Aujourd'hui/ }),
  ).toBeVisible();
  await expect(page.getByText(/1 fait\(s\) actif\(s\)/)).toBeVisible();
});
