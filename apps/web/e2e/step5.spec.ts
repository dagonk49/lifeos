import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect, test } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");
const ACCOUNT = "agenda.demo@example.org";

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

test("Step 5 journey: connect an account, authorise it per space, disconnect it", async ({ page }) => {
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E étape 5");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { name: /^Aujourd'hui/ })).toBeVisible();

  // Connect: the fake provider ("Démo") agrees at once and sends the browser back with a code.
  await nav(page, "Réglages");
  const panel = page.locator("#connexions");
  await expect(panel.getByText(/LifeOS n'accède à aucun agenda ni aucune boîte e-mail/)).toBeVisible();
  const usages = panel.getByRole("group", { name: "Usages autorisés dans cet espace" });
  await expect(usages.getByRole("checkbox", { name: "Lire l'agenda" })).toBeChecked();
  await usages.getByRole("checkbox", { name: "Lire les e-mails" }).check();
  await panel.getByRole("button", { name: "Connecter un compte Démo" }).click();

  await expect(page.getByText(`${ACCOUNT} est connecté.`)).toBeVisible();
  // The single-use code no longer shows in the address bar (nor in the history).
  await expect(page).toHaveURL(/\/connexions\/retour$/);
  await expect(page.getByText("Autorisé dans l'espace « Personnel » : Lire l'agenda et lire les e-mails.")).toBeVisible();
  await page.getByRole("link", { name: "Revenir aux comptes connectés" }).click();

  // The owner sees what is authorised, and where.
  const account = panel.getByRole("listitem").filter({ hasText: ACCOUNT });
  await expect(account.getByText("Connecté", { exact: true })).toBeVisible();
  await expect(account.getByText(/Accordé chez Démo : Lire l'agenda et lire les e-mails/)).toBeVisible();
  const personal = account.getByRole("group", { name: "Espace « Personnel »" });
  await expect(personal.getByRole("checkbox", { name: "Lire l'agenda" })).toBeChecked();
  await expect(personal.getByRole("checkbox", { name: "Lire les e-mails" })).toBeChecked();
  const work = account.getByRole("group", { name: "Espace « Professionnel »" });
  await expect(work.getByRole("checkbox", { name: "Lire l'agenda" })).not.toBeChecked();

  // Another space: already granted by the provider, authorised in LifeOS (recent passkey).
  await work.getByRole("checkbox", { name: "Lire l'agenda" }).click();
  await expect(work.getByRole("checkbox", { name: "Lire l'agenda" })).toBeChecked();
  // Withdrawing is immediate.
  await personal.getByRole("checkbox", { name: "Lire les e-mails" }).click();
  await expect(personal.getByRole("checkbox", { name: "Lire les e-mails" })).not.toBeChecked();

  // Disconnect: asked each time, the synced data is kept by default.
  await account.getByRole("button", { name: "Déconnecter…" }).click();
  const confirm = account.getByRole("group", { name: `Déconnecter ${ACCOUNT} ?` });
  await expect(confirm.getByRole("radio", { name: /Les garder/ })).toBeChecked();
  await confirm.getByRole("button", { name: `Déconnecter ${ACCOUNT}` }).click();
  await expect(panel.getByText(`${ACCOUNT} est déconnecté. Les données déjà synchronisées sont conservées.`)).toBeVisible();
  await expect(account.getByText("Déconnecté", { exact: true })).toBeVisible();
  await expect(account.getByRole("checkbox")).toHaveCount(0);
  await expect(page.getByText("connector.disconnect").first()).toBeVisible();

  // Then delete what was kept.
  await account.getByRole("button", { name: "Supprimer…" }).click();
  await account.getByRole("button", { name: `Supprimer ${ACCOUNT}` }).click();
  await expect(panel.getByText(/LifeOS n'accède à aucun agenda ni aucune boîte e-mail/)).toBeVisible();
});
