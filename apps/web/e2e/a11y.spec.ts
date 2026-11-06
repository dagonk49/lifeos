import type { Page } from "@playwright/test";

import { audit, formatIssues } from "./a11y-audit";
import { deleteJournalAndReviews, deleteTasksTitled, enrol, nav, openPage, pageAt, PAGES } from "./a11y-helpers";
import { expect, test } from "./fixtures";

/**
 * WCAG 2.2 AA checks (e2e/a11y-audit.ts) on every page, in the light and the dark theme.
 * Runs in the « chromium » and « mobile-chromium » projects (desktop and phone).
 */

async function expectNoIssues(page: Page, where: string): Promise<void> {
  const issues = await audit(page);
  // Soft: every page is checked, and the report lists all of them at once.
  expect.soft(issues, `${where}\n${formatIssues(issues)}`).toEqual([]);
}

test("every page meets the WCAG 2.2 AA checks, light and dark", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  await page.goto("/login");
  await expect(page).toHaveTitle("Connexion · LifeOS");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await expectNoIssues(page, `/login (${scheme})`);
  }
  await page.emulateMedia({ colorScheme: "light" });
  await enrol(page, `Navigateur a11y ${testInfo.project.name}`);
  await expectNoIssues(page, "/ (light, after enrolment)");

  // Some content, so that rows, checkboxes and badges are checked too: an overdue task, a
  // journal entry and a review (deleted at the end: the other journeys expect no open task
  // and a single journal entry of theirs to be there).
  const reviews: string[] = [];
  try {
    await nav(page, "Tâches");
    await page.getByLabel("Intitulé").fill(TASK);
    await page.getByLabel("Échéance").fill("2020-01-06T09:00");
    await page.getByRole("button", { name: "Ajouter", exact: true }).click();
    await expect(page.getByText("En retard").first()).toBeVisible();
    await nav(page, "Journal");
    await page.getByLabel("Faits, accomplissements, difficultés…").fill(ENTRY);
    await page.getByRole("button", { name: "Enregistrer" }).click();
    await expect(page.getByText(ENTRY)).toBeVisible();
    await nav(page, "Bilans");
    // « Établir à nouveau » when another journey (step3, on the desktop) already made this
    // week's review: it is then replaced.
    await page.getByRole("button", { name: /^Établir (le bilan|à nouveau)$/ }).click();
    await expect(page.getByRole("region", { name: "Faits (calculés localement, sans IA)" })).toBeVisible();
    await auditEveryPage(page, reviews);
  } finally {
    await deleteTasksTitled(page, TASK);
    await deleteJournalAndReviews(page, ENTRY, reviews);
  }
});

const TASK = "Rendre le dossier a11y";
const ENTRY = "Journée de vérification a11y";

async function auditEveryPage(page: Page, reviews: string[]): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const target of PAGES) {
      await openPage(page, target);
      await expectNoIssues(page, `${target.path} (${scheme})`);
    }
    // The memory tabs and the notifications panel.
    await openPage(page, pageAt("/memory"));
    for (const tab of ["Souvenirs", "À vérifier"]) {
      await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
      await expectNoIssues(page, `/memory, onglet ${tab} (${scheme})`);
    }
    await page.getByRole("button", { name: /^Notifications/ }).click();
    await expect(page.getByRole("group", { name: "Notifications" })).toBeVisible();
    await expectNoIssues(page, `notifications (${scheme})`);
    await page.keyboard.press("Escape");
    // « Discuter de… » (task 24): the assistant with the chosen task's chip.
    await openPage(page, pageAt("/tasks"));
    await page.getByRole("link", { name: `Discuter de « ${TASK} »` }).click();
    await expect(
      page.getByRole("group", { name: "Élément joint au message" }).getByText(`À propos de « ${TASK} » (Tâche)`),
    ).toBeVisible();
    await expectNoIssues(page, `/chat?about=task (${scheme})`);
    // « Discuter de cette entrée » and « Discuter de ce bilan »: their chips, and the link
    // under the message once sent.
    const chip = page.getByRole("group", { name: "Élément joint au message" });
    await openPage(page, pageAt("/journal"));
    await page
      .locator("section")
      .filter({ hasText: ENTRY })
      .getByRole("link", { name: /^Discuter de cette entrée du / })
      .click();
    await expect(chip.getByText(/^À propos de « .+ » \(Entrée de journal\)$/)).toBeVisible();
    await expectNoIssues(page, `/chat?about=journal (${scheme})`);
    await openPage(page, pageAt("/reviews"));
    await page.getByRole("link", { name: "Discuter de ce bilan" }).click();
    await expect(page).toHaveURL(/\/chat\?about=review:[0-9a-f-]{36}$/);
    const id = new URL(page.url()).searchParams.get("about")?.split(":")[1];
    if (id && !reviews.includes(id)) reviews.push(id);
    await expect(chip.getByText(/^À propos de « .+ » \(Bilan\)$/)).toBeVisible();
    await expectNoIssues(page, `/chat?about=review (${scheme})`);
    if (scheme === "dark") {
      await page.getByLabel("Votre message").fill("Que retenir ?");
      await page.getByRole("button", { name: "Envoyer" }).click();
      await expect(page.getByRole("list", { name: "Messages" }).getByRole("link", { name: "Bilan lié" })).toBeVisible();
      await expectNoIssues(page, `/chat, message lié à un bilan (${scheme})`);
    }
  }
}
