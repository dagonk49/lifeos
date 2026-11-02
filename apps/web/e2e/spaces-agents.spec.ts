import { expect, test } from "./fixtures";
import { enrol, messages, nav, newConversation } from "./helpers";

const INVOICE = `FACTURE N° E2E-PRO
Prestation de conseil, octobre
Total à payer : 1 200,00 EUR
`;

test("§9: a professional attachment stays out of reach of the Finance agent without permission", async ({ page }) => {
  test.setTimeout(120_000);
  await enrol(page, "Navigateur E2E espaces");
  // The professional space, with the AI allowed there (the agent's own access is separate).
  await page.getByRole("combobox", { name: "Espace", exact: true }).selectOption({ label: "Professionnel" });
  await nav(page, "Réglages");
  const work = page.getByRole("listitem").filter({ hasText: "Professionnel" }).filter({ hasText: "IA" });
  const allow = work.getByRole("button", { name: "Autoriser l'IA" });
  await expect(allow.or(work.getByText("IA autorisée"))).toBeVisible();
  if (await allow.isVisible()) await allow.click();
  await expect(work.getByText("IA autorisée")).toBeVisible();

  await nav(page, "Assistant");
  await newConversation(page);
  await expect(page.getByText(/Espace : Professionnel/).first()).toBeVisible();
  await page.getByLabel("Agent").selectOption({ label: "Finances" });
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Joindre un document" }).click();
  await (await chooser).setFiles({ name: "facture-pro.txt", mimeType: "text/plain", buffer: Buffer.from(INVOICE, "utf8") });
  await expect(page.getByRole("list", { name: "Pièces jointes" }).getByText("Texte prêt")).toBeVisible({
    timeout: 30_000,
  });

  // The control before sending says it: the agent may not read this space's documents.
  await page.getByLabel("Votre message").fill("Que dit cette facture ?");
  await page.getByRole("button", { name: "Envoyer", exact: true }).click();
  const review = page.getByRole("region", { name: "Avant l'envoi à l'assistant IA" });
  await expect(review).toContainText(
    "« facture-pro » : reste sur votre serveur (cet agent n'a pas accès aux documents de cet espace)",
  );
  await review.getByRole("button", { name: "Confirmer l'envoi" }).click();
  // The message is saved without the document; the agent first asks for its access.
  await expect(page.getByText("Autoriser « Finances » dans cet espace ?")).toBeVisible();
  await expect(messages(page).getByText("Que dit cette facture ?", { exact: true })).toBeVisible();
  await expect(page.getByRole("list", { name: "Pièces jointes envoyées" })).toHaveCount(0);
  await expect(messages(page).getByText(/^\[fake\]/)).toHaveCount(0);
});
