import { expect, test } from "./fixtures";
import { addVirtualAuthenticator, enrollmentToken, nav } from "./helpers";

// Today in the browser's and the owner's zone (Europe/Paris), not the test runner's: around
// midnight the two dates differ.
function todayAtNoon(): string {
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Paris" }).format(new Date());
  return `${today}T12:00`;
}

test("Task 23 journey: home cards hidden and kept, week view with today's task", async ({ page }) => {
  test.setTimeout(90_000);
  await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await page.getByLabel("Nom de l'appareil").fill("Navigateur E2E accueil");
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();

  // A task due today at noon: in the current week whatever the week start.
  await nav(page, "Tâches");
  await page.getByLabel("Intitulé").fill("Préparer la semaine E2E");
  await page.getByLabel("Échéance").fill(todayAtNoon());
  await page.getByRole("button", { name: "Ajouter" }).click();
  await expect(page.getByRole("checkbox", { name: "Terminer « Préparer la semaine E2E »" })).toBeVisible();
  await nav(page, "Accueil");
  await expect(page).toHaveURL(/\/$/);

  // Hide a card: kept on the server, so still hidden after a reload.
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Personnaliser" }).click();
  await page.getByRole("checkbox", { name: "Afficher « Mémoire »" }).uncheck();
  await page.getByRole("button", { name: "Monter « Suggestions »" }).click();
  await page.getByRole("button", { name: "Enregistrer la disposition" }).click();
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Suggestions", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toHaveCount(0);

  // The week view, kept as the default view, shows the task on today's column.
  await page.getByRole("button", { name: "Semaine", exact: true }).click();
  const today = page.getByRole("region").filter({ has: page.getByText("· aujourd'hui") });
  await expect(today.getByRole("link", { name: "Préparer la semaine E2E" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Semaine", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(today.getByRole("link", { name: "Préparer la semaine E2E" })).toBeVisible();
  await expect(today.getByText(/^Échéance \d\d:\d\d$/).first()).toBeVisible();

  // Back to the defaults for the journeys that follow.
  await page.getByRole("button", { name: "Jour", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agenda du jour" })).toBeVisible();
  await page.getByRole("button", { name: "Personnaliser" }).click();
  await page.getByRole("button", { name: "Rétablir l'ordre par défaut" }).click();
  await page.getByRole("button", { name: "Enregistrer la disposition" }).click();
  await expect(page.getByRole("heading", { name: "Mémoire", exact: true })).toBeVisible();
});
