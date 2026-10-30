import type { Page } from "@playwright/test";

import { audit, formatIssues } from "./a11y-audit";
import { deleteTasksTitled, enrol, nav, openPage, pageAt, PAGES } from "./a11y-helpers";
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

// --- The installed web app is the mobile client (ADR 0022) ---------------------------------

/** Waits until the service worker of /sw.js (registered on every page) controls the page. */
async function waitForWorker(page: Page): Promise<void> {
  await page.waitForFunction(() => navigator.serviceWorker.controller?.scriptURL.endsWith("/sw.js") === true);
}

// A 1 × 1 PNG: the « photo » shared from the gallery.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test("the manifest offers the share sheet and quick shortcuts; /partage without a worker drops the files", async ({
  request,
}) => {
  const manifest = (await (await request.get("/manifest.webmanifest")).json()) as {
    id: string;
    share_target: { action: string; method: string; enctype: string; params: { files: { name: string; accept: string[] }[] } };
    shortcuts: { url: string }[];
  };
  expect(manifest.id).toBe("/");
  expect(manifest.share_target).toMatchObject({ action: "/partage", method: "POST", enctype: "multipart/form-data" });
  expect(manifest.share_target.params.files[0]?.name).toBe("file");
  expect(manifest.share_target.params.files[0]?.accept).toEqual(expect.arrayContaining(["image/jpeg", "application/pdf"]));
  expect(manifest.shortcuts.map((s) => s.url)).toEqual(["/tasks", "/chat", "/documents"]);

  // No worker yet (first visit): the server keeps nothing and asks to pick the file again.
  const shared = await request.post("/partage", {
    multipart: { file: { name: "ticket.png", mimeType: "image/png", buffer: PNG } },
    maxRedirects: 0,
  });
  expect(shared.status()).toBe(303);
  expect(shared.headers()["location"]).toBe("/documents?partage=indisponible");
});

test("offline, a navigation shows a fixed page without data, accessible, then « Réessayer » reloads", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(120_000);
  await enrol(page, `Téléphone hors connexion ${testInfo.project.name}`);
  await waitForWorker(page);
  await context.setOffline(true);
  try {
    await page.goto("/tasks");
    await expect(page).toHaveTitle("Hors connexion · LifeOS");
    await expect(page.getByRole("heading", { level: 1, name: "LifeOS n’est pas joignable" })).toBeVisible();
    // Built by the worker: no script, nothing read from the API or kept on the phone.
    await expect(page.locator("script")).toHaveCount(0);
    expect(await page.evaluate(() => caches.keys())).toEqual([]);
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      const issues = await audit(page);
      expect.soft(issues, `page hors connexion (${scheme})\n${formatIssues(issues)}`).toEqual([]);
    }
    await page.emulateMedia({ colorScheme: "light" });
  } finally {
    await context.setOffline(false);
  }
  await page.getByRole("link", { name: "Réessayer" }).tap();
  await expect(page).toHaveTitle(pageAt("/tasks").title);
});

test("a photo shared from the phone opens the upload form; nothing is sent before the owner", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  await enrol(page, `Téléphone partage ${testInfo.project.name}`);
  await waitForWorker(page);
  // What Android does on « Partager → LifeOS »: a multipart POST navigation to the share
  // target of the manifest, answered by the service worker.
  await page.evaluate(() => {
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/partage";
    form.enctype = "multipart/form-data";
    const input = document.createElement("input");
    input.type = "file";
    input.name = "file";
    input.id = "e2e-share";
    form.append(input);
    document.body.append(form);
  });
  await page.setInputFiles("#e2e-share", { name: "ticket-partage.png", mimeType: "image/png", buffer: PNG });
  const uploads: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/v1/documents") uploads.push(r.url());
  });
  await page.evaluate(() => (document.getElementById("e2e-share")?.closest("form") as HTMLFormElement).submit());

  // The query parameter is dropped once the files are taken: a reload does not ask again.
  await expect(page).toHaveURL(/\/documents$/);
  await expect(page).toHaveTitle(pageAt("/documents").title);
  await expect(
    page.getByText("Fichier partagé : choisissez son type (document ou ticket de caisse) et son espace, puis envoyez-le."),
  ).toBeVisible();
  await expect(page.getByText("ticket-partage.png")).toBeVisible();
  await expect(page.getByRole("radio", { name: "Ticket de caisse" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Envoyer", exact: true })).toBeEnabled();
  const issues = await audit(page);
  expect.soft(issues, `/documents après un partage\n${formatIssues(issues)}`).toEqual([]);
  expect(uploads).toEqual([]);

  // The worker handed the files over once: asking again gets nothing.
  await page.goto("/documents?partage=1");
  await expect(page.getByText(/^Le fichier partagé n'a pas pu être repris\./)).toBeVisible({ timeout: 15_000 });
});

test("the settings explain how to install LifeOS on the phone", async ({ page }, testInfo) => {
  await enrol(page, `Téléphone installation ${testInfo.project.name}`);
  await openPage(page, pageAt("/settings"));
  const section = page.locator("#installer");
  await expect(section.getByRole("heading", { name: "Installer LifeOS sur le téléphone" })).toBeVisible();
  // Chrome offers its own dialog when the app is installable; otherwise, the menu steps.
  await expect(
    section.getByRole("button", { name: "Installer LifeOS" }).or(section.getByText(/Dans le menu du navigateur/)),
  ).toBeVisible();
});
