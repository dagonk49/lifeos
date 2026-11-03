import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { allowPersonalAi, enrol, messages, nav, newConversation } from "./helpers";

async function send(page: Page, text: string): Promise<void> {
  await page.getByLabel("Votre message").fill(text);
  await page.getByRole("button", { name: "Envoyer", exact: true }).click();
}

/** The emergency stop, from the page (also used to lift it whatever the journey did). */
async function setPaused(page: Page, paused: boolean): Promise<void> {
  await page.evaluate(async (llm_paused) => {
    await fetch("/v1/settings", {
      method: "PATCH",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ llm_paused }),
    });
  }, paused);
}

test("AI paused (emergency stop): the message is kept, tasks, budget and home page still work", async ({ page }) => {
  test.setTimeout(120_000);
  await enrol(page, "Navigateur E2E pause IA");
  await allowPersonalAi(page);
  const pause = page.getByRole("switch", { name: "Pause de l'assistant IA" });
  await pause.click();
  await expect(pause).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("En pause : aucun appel cloud n'est effectué.", { exact: false })).toBeVisible();
  try {
    await nav(page, "Assistant");
    await newConversation(page);
    await send(page, "Message pendant la pause");
    await expect(
      page.getByText(
        "Message enregistré, réponse indisponible : L'assistant IA est en pause (arrêt d'urgence). Réactivez-le dans les réglages.",
      ),
    ).toBeVisible();
    // Kept on the server: there after a reload.
    await page.reload();
    await page
      .getByRole("button", { name: /^Message pendant la pause/ })
      .first()
      .click();
    await expect(messages(page).getByText("Message pendant la pause", { exact: true })).toBeVisible();
    await expect(messages(page).getByText(/^\[fake\]/)).toHaveCount(0);

    // §9: a cloud outage blocks neither the tasks nor the local budget.
    await nav(page, "Tâches");
    await page.getByLabel("Intitulé").fill("Tâche pendant la pause");
    await page.getByRole("button", { name: "Ajouter", exact: true }).click();
    const task = page.getByRole("checkbox", { name: "Terminer « Tâche pendant la pause »" });
    await expect(task).toBeVisible();
    await task.click();
    await nav(page, "Finances");
    await page.getByLabel("Espace du budget").selectOption({ label: "Personnel" });
    await expect(page.getByRole("heading", { level: 2, name: /^Budget .* · Personnel$/ })).toBeVisible();
    await expect(page.getByText("Solde", { exact: true }).first()).toBeVisible();
    await nav(page, "Accueil");
    await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();
  } finally {
    await setPaused(page, false);
  }
  await nav(page, "Réglages");
  await expect(page.getByRole("switch", { name: "Pause de l'assistant IA" })).toHaveAttribute("aria-checked", "false");
});

test("AI provider unreachable: the message is kept with a clear degraded answer", async ({ page }) => {
  test.setTimeout(90_000);
  await enrol(page, "Navigateur E2E panne IA");
  await allowPersonalAi(page);
  await nav(page, "Assistant");
  await newConversation(page);
  // The fake provider fails on this prefix, as an unreachable cloud provider would.
  await send(page, "[fake:indisponible] Aide-moi pour la semaine");
  await expect(
    page.getByText(
      "Message enregistré, réponse indisponible : L'assistant IA est indisponible pour le moment. Vos données restent accessibles.",
    ),
  ).toBeVisible();
  await expect(messages(page).getByText("[fake:indisponible] Aide-moi pour la semaine", { exact: true })).toBeVisible();
  // Once the provider answers again, nothing stays blocked (a new conversation: messages
  // left without an answer are sent again with the next one).
  await newConversation(page);
  await send(page, "Et maintenant ?");
  await expect(messages(page).getByText("[fake] Et maintenant ?")).toBeVisible();
});
