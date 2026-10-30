import type { Page } from "@playwright/test";

import { audit, formatIssues } from "./a11y-audit";
import { deleteTasksTitled, enrol, nav, openPage, pageAt, PAGES } from "./a11y-helpers";
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

  // Some content, so that rows, checkboxes and badges are checked too: an overdue task
  // (deleted at the end: the other journeys expect no open task of theirs to be there).
  try {
    await nav(page, "Tâches");
    await page.getByLabel("Intitulé").fill(TASK);
    await page.getByLabel("Échéance").fill("2020-01-06T09:00");
    await page.getByRole("button", { name: "Ajouter", exact: true }).click();
    await expect(page.getByText("En retard").first()).toBeVisible();
    await auditEveryPage(page);
  } finally {
    await deleteTasksTitled(page, TASK);
  }
});

const TASK = "Rendre le dossier a11y";

async function auditEveryPage(page: Page): Promise<void> {
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
  }
}
