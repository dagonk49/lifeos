import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { expect, test } from "../fixtures";
import { addVirtualAuthenticator, enrol, nav, STACK_DIR } from "../helpers";

/**
 * Restore with a browser (scripts/test-e2e-stack.sh): phase « backup » leaves data and keeps
 * the passkey of the virtual authenticator (its private key: a throw-away stack's, never
 * uploaded with the reports); the script backs up, deletes the volumes and restores; phase
 * « restored » signs in with that same passkey and finds everything.
 */
const PHASE = process.env.LIFEOS_E2E_PHASE ?? "";
const STATE = process.env.LIFEOS_E2E_STATE ?? join(tmpdir(), "lifeos-e2e-stack-state.json");
const TASK = "Tâche d'avant la sauvegarde";
const MERCHANT = "Boulangerie de la restauration";
const DOCUMENT = { name: "attestation-restauration.txt", text: "Attestation de test pour la restauration.\n" };

interface Credential {
  credentialId: string;
  isResidentCredential: boolean;
  rpId: string;
  privateKey: string;
  userHandle?: string;
  signCount: number;
}

test.skip(!STACK_DIR, "production-like stack only (scripts/test-e2e-stack.sh)");

async function documentRegion(page: Page) {
  await nav(page, "Documents");
  // Wait for that navigation to land: a slower one finishing after the document's link
  // was clicked would bring the page back to /documents and close the document.
  await expect(page).toHaveURL(/\/documents$/);
  await expect(page.getByRole("region", { name: "Mes documents" })).toBeVisible();
  return page.getByRole("region", { name: "attestation-restauration" });
}

/** The document's detail, opened from the list if the page shows another one. */
async function openDocument(page: Page) {
  const region = await documentRegion(page);
  const link = page.getByRole("main").getByRole("link", { name: /attestation-restauration/ }).first();
  // Right after an upload the list may not show the document yet, and right after a restore
  // the first loads are slow: retry, reloading only from the second attempt on.
  let attempt = 0;
  await expect(async () => {
    attempt += 1;
    if (await region.isVisible()) return;
    if (attempt > 1) await page.reload();
    await link.click({ timeout: 20_000 });
    await expect(page).toHaveURL(/\/documents\?id=/, { timeout: 5_000 });
    await expect(region).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 90_000 });
  return region;
}

test("before the backup: data and the passkey kept aside", async ({ page }) => {
  test.skip(PHASE !== "backup", "phase « backup » of scripts/test-e2e-stack.sh");
  test.setTimeout(120_000);
  const { cdp, authenticatorId } = await enrol(page, "Navigateur E2E restauration");

  await nav(page, "Tâches");
  await page.getByLabel("Intitulé").fill(TASK);
  await page.getByRole("button", { name: "Ajouter", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: `Terminer « ${TASK} »` })).toBeVisible();

  await nav(page, "Finances");
  await page.getByRole("button", { name: "Nouvelle opération" }).click();
  // The form's own space field (the header has one too, first in the page).
  await page.getByRole("combobox", { name: "Espace", exact: true }).last().selectOption({ label: "Personnel" });
  await page.getByLabel("Montant").fill("12,40");
  await page.getByLabel("Commerçant").fill(MERCHANT);
  await page.getByRole("button", { name: "Ajouter l'opération" }).click();
  await expect(page.getByText(MERCHANT).first()).toBeVisible();

  await nav(page, "Documents");
  await page.getByLabel("Fichier", { exact: true }).setInputFiles({
    name: DOCUMENT.name,
    mimeType: "text/plain",
    buffer: Buffer.from(DOCUMENT.text, "utf8"),
  });
  await page.getByRole("radio", { name: "Document", exact: true }).first().check();
  await page.getByRole("button", { name: "Envoyer", exact: true }).click();
  await expect((await openDocument(page)).getByText("Prêt", { exact: true })).toBeVisible({ timeout: 30_000 });

  // The passkey, as the owner's phone would keep it through the server's loss.
  const { credentials } = await cdp.send("WebAuthn.getCredentials", { authenticatorId });
  expect(credentials).toHaveLength(1);
  writeFileSync(STATE, JSON.stringify(credentials[0]), { mode: 0o600 });
});

test("after the restore: the same passkey signs in, task, transaction and original are back", async ({ page }) => {
  test.skip(PHASE !== "restored", "phase « restored » of scripts/test-e2e-stack.sh");
  test.setTimeout(120_000);
  const credential = JSON.parse(readFileSync(STATE, "utf8")) as Credential;
  const { cdp, authenticatorId } = await addVirtualAuthenticator(page);
  await cdp.send("WebAuthn.addCredential", { authenticatorId, credential });

  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("button", { name: "Se connecter avec une passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();

  await nav(page, "Tâches");
  await expect(page.getByRole("checkbox", { name: `Terminer « ${TASK} »` })).toBeVisible();
  await nav(page, "Finances");
  await expect(page.getByText(MERCHANT).first()).toBeVisible();
  const region = await openDocument(page);
  const download = page.waitForEvent("download");
  await region.getByRole("link", { name: "Télécharger l'original" }).click();
  // The encrypted original, restored with the documents key, decrypts to the same bytes.
  expect(readFileSync(await (await download).path(), "utf8")).toBe(DOCUMENT.text);
});

