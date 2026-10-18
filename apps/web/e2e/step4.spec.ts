import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect, test } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");

const RECEIPT = `CARREFOUR MARKET
12 rue de la Paix
LAIT DEMI ECREME 1L        1,15
PAIN DE CAMPAGNE           2,30
POMMES GOLDEN 1,2 KG       2,98
TOTAL TTC                  6,43 EUR
CB EMV  **** **** **** 1234
10/10/2026 18:42  CAISSE 3`;
const RIB = `RELEVÉ D'IDENTITÉ BANCAIRE
Titulaire : Camille Martin
IBAN : FR76 3000 6000 0112 3456 7890 189
BIC : AGRIFRPP`;

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

async function nav(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name }).click();
}

async function uploadText(page: Page, name: string, text: string, purpose: "Document" | "Ticket de caisse") {
  await page.getByLabel("Fichier", { exact: true }).setInputFiles({
    name,
    mimeType: "text/plain",
    buffer: Buffer.from(text, "utf8"),
  });
  await page.getByRole("radio", { name: purpose, exact: true }).first().check();
  await page.getByRole("button", { name: "Envoyer", exact: true }).click();
}

test("Step 4 journey: receipt to budget, sensitive document kept local, dictated journal", async ({ page }) => {
  test.setTimeout(120_000);
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E étape 4");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  // UC02: a receipt is read locally (worker), checked, then added to the budget.
  await nav(page, "Documents");
  await uploadText(page, "ticket-carrefour.txt", RECEIPT, "Ticket de caisse");
  const detail = page.getByRole("region", { name: "ticket-carrefour" });
  await expect(detail.getByText("Prêt", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(detail.getByText("CARREFOUR MARKET").first()).toBeVisible();
  await expect(detail.getByText("Ligne lue : « TOTAL TTC 6,43 EUR »")).toBeVisible();
  await detail.getByRole("link", { name: "Ajouter aux finances" }).click();
  await expect(page).toHaveURL(/\/finances\?receipt=/);
  await expect(page.getByLabel("Montant")).toHaveValue("6,43");
  await expect(page.getByLabel("Commerçant")).toHaveValue("CARREFOUR MARKET");
  await page.getByRole("button", { name: "Ajouter l'opération" }).click();
  await expect(page.getByRole("list", { name: /Opérations/ }).getByText("CARREFOUR MARKET", { exact: true })).toBeVisible();
  await expect(page.getByText("−6,43 €").first()).toBeVisible();

  // F10: a bank document is recognised locally as sensitive, and is never attached.
  await nav(page, "Documents");
  await uploadText(page, "rib.txt", RIB, "Document");
  const rib = page.getByRole("region", { name: "rib" });
  await expect(rib.getByText("Banque", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  await expect(rib.getByText(/jamais envoyé à l'assistant IA/).first()).toBeVisible();

  await nav(page, "Assistant");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Joindre un document" }).click();
  await (await chooser).setFiles({ name: "rib-chat.txt", mimeType: "text/plain", buffer: Buffer.from(RIB, "utf8") });
  const attachments = page.getByRole("list", { name: "Pièces jointes" });
  await expect(attachments.getByText(/Document sensible \(banque\) : jamais envoyé à l'IA/)).toBeVisible({
    timeout: 30_000,
  });

  // UC01: the day is dictated, transcribed on the server, reviewed, then saved.
  await nav(page, "Journal");
  await page.getByRole("button", { name: "Dicter ma journée" }).click();
  // Speak for a moment (the fake microphone plays a tone) before stopping.
  await expect(page.getByText(/Enregistrement 0:0[1-9]/)).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Terminer la dictée" }).click();
  const content = page.getByLabel("Faits, accomplissements, difficultés…");
  await expect(content).toHaveValue(/Dictée de test\./, { timeout: 30_000 });
  await expect(page.getByText(/relisez-le et corrigez-le/)).toBeVisible();
  await content.fill("Dictée de test. Corrigée : course de 5 km ce matin.");
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(page.getByText("Dictée de test. Corrigée : course de 5 km ce matin.")).toBeVisible();
});
