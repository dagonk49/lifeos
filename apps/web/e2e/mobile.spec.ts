import type { Page } from "@playwright/test";

import { deleteTasksTitled, enrol, nav, openPage, PAGES } from "./a11y-helpers";
import { expect, test } from "./fixtures";

/**
 * Phone use (« mobile-chromium » project only: touch screen, coarse pointer): no page is
 * wider than the screen (WCAG 1.4.10 reflow), and the main journeys work by tapping.
 */

async function expectNoHorizontalScroll(page: Page, where: string): Promise<void> {
  const sizes = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect.soft(sizes.scroll, `${where} : ${sizes.scroll} px de large pour ${sizes.client} px`).toBeLessThanOrEqual(sizes.client);
}

test("no page scrolls sideways at 360 and 375 px", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/login");
  await expectNoHorizontalScroll(page, "/login (360 px)");
  await enrol(page, `Téléphone ${testInfo.project.name}`);
  for (const width of [360, 375]) {
    await page.setViewportSize({ width, height: 740 });
    for (const target of PAGES) {
      await openPage(page, target);
      await expectNoHorizontalScroll(page, `${target.path} (${width} px)`);
    }
    // The notifications panel stays on the screen.
    await page.getByRole("button", { name: /^Notifications/ }).tap();
    const panel = page.getByRole("group", { name: "Notifications" });
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(width);
    await page.getByRole("button", { name: /^Notifications/ }).tap();
  }
});

test("touch journeys: sign in, add a task, write and dictate a message, scan a receipt", async ({
  page,
}, testInfo) => {
  test.setTimeout(150_000);
  const task = `Arroser les plantes (${testInfo.project.name})`;
  await enrol(page, `Téléphone tactile ${testInfo.project.name}`);
  try {
    // Sign out, then back in with the passkey, by tapping.
    await page.getByRole("button", { name: "Déconnexion" }).tap();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByRole("button", { name: "Se connecter avec une passkey" }).tap();
    await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();

    // A task.
    await nav(page, "Tâches");
    await page.getByLabel("Intitulé").fill(task);
    await page.getByRole("button", { name: "Ajouter", exact: true }).tap();
    await expect(page.getByRole("checkbox", { name: `Terminer « ${task} »` })).toBeVisible();

    // The assistant: on a touch screen Enter goes to the next line, the button sends.
    await nav(page, "Assistant");
    await expect(page.getByRole("button", { name: "Afficher les conversations" })).toBeVisible();
    const box = page.getByLabel("Votre message");
    await expect(page.getByText("Entrée va à la ligne ; le bouton « Envoyer » envoie le message.")).toBeVisible();
    await box.fill("Première ligne");
    await box.press("Enter");
    await box.pressSequentially("Deuxième ligne");
    await expect(box).toHaveValue("Première ligne\nDeuxième ligne");
    await page.getByRole("button", { name: "Envoyer", exact: true }).tap();
    await expect(page.getByRole("list", { name: "Messages" }).getByText(/^Première ligne\s+Deuxième ligne$/)).toBeVisible();

    // Dictation (fake microphone, fake transcription).
    await page.getByRole("button", { name: "Dicter", exact: true }).tap();
    await expect(page.getByText(/Enregistrement 0:0[1-9]/)).toBeVisible({ timeout: 10_000 });
    await page.getByRole("button", { name: "Terminer la dictée" }).tap();
    await expect(box).toHaveValue(/Dictée de test\./, { timeout: 30_000 });

    // A receipt photo: the file input asks the phone for its rear camera.
    await nav(page, "Finances");
    await page.getByRole("button", { name: "Scanner un ticket" }).tap();
    await expect(page.getByLabel("Photo ou PDF du ticket")).toHaveAttribute("capture", "environment");
  } finally {
    await deleteTasksTitled(page, task);
  }
});
