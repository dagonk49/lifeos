import type { Page } from "@playwright/test";

import { deleteTasksTitled } from "./a11y-helpers";
import { expect, test } from "./fixtures";
import { enrol, nav, runJob, sql } from "./helpers";

const FIRST = "Arroser le basilic E2E";
const QUIET = "Sortir les poubelles E2E";

/** `now` + `minutes` in the owner's time zone: "YYYY-MM-DDTHH:MM" and "HH:MM". */
function localTime(timeZone: string, minutes = 0): { dateTime: string; time: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(Date.now() + minutes * 60_000))
      .map((part) => [part.type, part.value]),
  );
  const time = `${parts.hour}:${parts.minute}`;
  return { dateTime: `${parts.year}-${parts.month}-${parts.day}T${time}`, time };
}

async function ownerTimeZone(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const response = await fetch("/v1/settings", { headers: { Accept: "application/json" } });
    return ((await response.json()) as { timezone: string }).timezone;
  });
}

/** A task whose reminder is due now (the browser runs in the owner's time zone). */
async function taskWithReminder(page: Page, title: string, timeZone: string): Promise<void> {
  await nav(page, "Tâches");
  await page.getByLabel("Intitulé").fill(title);
  await page.getByLabel("Rappel", { exact: true }).fill(localTime(timeZone).dateTime);
  await page.getByRole("button", { name: "Ajouter", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: `Terminer « ${title} »` })).toBeVisible();
}

function notificationsButton(page: Page) {
  return page.getByRole("button", { name: /^Notifications/ });
}

test("a due reminder becomes an in-app notification; quiet hours hold the next one back", async ({ page }) => {
  test.setTimeout(150_000);
  await enrol(page, "Navigateur E2E rappels");
  const timeZone = await ownerTimeZone(page);
  try {
    // The reminder job (every minute in production) runs now.
    await taskWithReminder(page, FIRST, timeZone);
    runJob("send_reminders");
    await page.reload();
    await expect(notificationsButton(page)).toHaveAccessibleName(/^Notifications ?, \d+ en attente$/);
    await notificationsButton(page).click();
    const panel = page.getByRole("group", { name: "Notifications" });
    await expect(panel.getByText(`Rappel : ${FIRST}`)).toBeVisible();
    await panel.getByRole("button", { name: `Marquer « Rappel : ${FIRST} » comme lu` }).click();
    await expect(panel.getByText(`Rappel : ${FIRST}`)).toHaveCount(0);

    // Quiet hours around the current time (computed in the owner's zone, so that no
    // midnight or daylight-saving change can make them miss now).
    await nav(page, "Réglages");
    // The form of « Préférences » (other sections have their own « Enregistrer »).
    const preferences = page.locator("form").filter({ has: page.getByLabel("Tranquillité : début") });
    // Filled from the saved settings first: typing sooner would be overwritten.
    await expect(preferences.getByLabel("Fuseau horaire")).toHaveValue(timeZone);
    await preferences.getByLabel("Tranquillité : début").fill(localTime(timeZone, -60).time);
    await preferences.getByLabel("Tranquillité : fin").fill(localTime(timeZone, 60).time);
    await preferences.getByRole("button", { name: "Enregistrer" }).click();
    await expect(preferences.getByText("Enregistré", { exact: true })).toBeVisible();

    await taskWithReminder(page, QUIET, timeZone);
    runJob("send_reminders");
    await page.reload();
    await notificationsButton(page).click();
    await expect(page.getByRole("group", { name: "Notifications" }).getByText(`Rappel : ${QUIET}`)).toHaveCount(0);
    // Created, but held until the end of the quiet hours (an important notification, not
    // a critical alert): postponed, never dropped.
    const [held] = sql(
      `SELECT deliver_after > now() AS held FROM notifications WHERE title = 'Rappel : ${QUIET}'`,
    );
    expect(held).toEqual({ held: true });
  } finally {
    // No quiet hours for the other journeys, and the held reminder (due again once they
    // are lifted) dismissed: the specs share one database.
    await page.evaluate(async (title) => {
      const json = { Accept: "application/json", "Content-Type": "application/json" };
      await fetch("/v1/settings", {
        method: "PATCH",
        headers: json,
        body: JSON.stringify({ quiet_hours_start: null, quiet_hours_end: null }),
      });
      const response = await fetch("/v1/notifications", { headers: json });
      const items = response.ok ? ((await response.json()) as { id: string; title: string }[]) : [];
      for (const item of items.filter((n) => n.title === title)) {
        await fetch(`/v1/notifications/${item.id}`, {
          method: "PATCH",
          headers: json,
          body: JSON.stringify({ status: "dismissed" }),
        });
      }
    }, `Rappel : ${QUIET}`);
    await deleteTasksTitled(page, FIRST);
    await deleteTasksTitled(page, QUIET);
  }
});
