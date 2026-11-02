import { expect, test } from "./fixtures";
import { adminCli, enrol, nav, OWNER_EMAIL, STACK_DIR } from "./helpers";

const EXPIRED = "Session expirée : reconnectez-vous pour reprendre là où vous en étiez.";

test("a session revoked on the server: login page, then back to the page with the draft", async ({ page }) => {
  test.setTimeout(90_000);
  await enrol(page, "Navigateur E2E session");
  await nav(page, "Assistant");
  const composer = page.getByLabel("Votre message");
  await composer.fill("Brouillon à ne pas perdre");

  // The server signs every session out (lost device, docs/exploitation.md), then the owner
  // acts: the request is refused and the page goes to the login page, asked to come back.
  adminCli("revoke-sessions", "--email", OWNER_EMAIL);
  await page.getByRole("button", { name: "Envoyer", exact: true }).click();
  await expect(page).toHaveURL(/\/login\?next=%2Fchat$/);
  await expect(page.getByText(EXPIRED)).toBeVisible();

  // Signed in again with the passkey: the same page, the message still being written.
  await page.getByRole("button", { name: "Se connecter avec une passkey" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByLabel("Votre message")).toHaveValue("Brouillon à ne pas perdre");
  await page.getByLabel("Votre message").fill("");

  // The session cookie: never readable by scripts, never sent cross-site, and over HTTPS
  // only on the production-like stack (LIFEOS_COOKIE_SECURE, true by default).
  const session = (await page.context().cookies()).find((cookie) => cookie.httpOnly);
  expect(session).toBeDefined();
  expect(session?.sameSite).toBe("Strict");
  if (STACK_DIR) expect(session?.secure).toBe(true);
});

test("a passkey revoked from another device signs that device out, its draft kept in its tab", async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000);
  await enrol(page, "Ordinateur E2E session");
  await nav(page, "Journal");
  const journal = page.getByLabel("Faits, accomplissements, difficultés…");
  await journal.fill("Journée à ne pas perdre");

  // Another device of the owner revokes the computer's passkey (Réglages): the sessions
  // opened with it go with it.
  const phoneContext = await browser.newContext({ baseURL: test.info().project.use.baseURL });
  try {
    const phone = await phoneContext.newPage();
    await enrol(phone, "Téléphone E2E session");
    await nav(phone, "Réglages");
    const revoke = phone.getByRole("button", { name: "Révoquer la passkey « Ordinateur E2E session »" });
    await revoke.click();
    await expect(revoke).toHaveCount(0);
  } finally {
    await phoneContext.close();
  }

  // The computer's next request is refused: login page, asked to come back to the journal.
  await page.reload();
  await expect(page).toHaveURL(/\/login\?next=%2Fjournal$/);
  await expect(page.getByText(EXPIRED)).toBeVisible();
  // Nothing of the session is shown, and the draft stays in this tab only (sessionStorage).
  await expect(page.getByRole("navigation", { name: "Navigation principale" })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("lifeos.draft.journal"))).toBe("Journée à ne pas perdre");
});
