import { readFileSync } from "node:fs";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { allowPersonalAi, enrol, messages, nav, newConversation, runJob, sql } from "./helpers";

const KEPT = "Recette du gâteau de mamie à conserver";
const GONE = "Note éphémère sur la zéphyrine du jardin";
const FACT = "Je collectionne les timbres zéphyrins";
const MEMORY = { title: "Souvenir protégé E2E", content: "Vacances à Zéphyrville en famille" };

async function send(page: Page, text: string): Promise<void> {
  await page.getByLabel("Votre message").fill(text);
  await page.getByRole("button", { name: "Envoyer", exact: true }).click();
  await expect(messages(page).getByText(`[fake] ${text}`)).toBeVisible();
}

/** Searches, waiting for the answer; returns the result items holding `text`. */
async function search(page: Page, query: string, text: string) {
  // A fresh page load: the same query is asked to the server again, never answered from the
  // cache of an earlier search.
  await page.goto("/search");
  await expect(page.getByRole("heading", { level: 1, name: "Recherche" })).toBeVisible();
  await page.getByLabel("Rechercher").fill(query);
  const answered = page.waitForResponse((response) => new URL(response.url()).pathname === "/v1/search");
  await page.getByRole("button", { name: "Rechercher" }).click();
  await answered;
  await expect(page.getByRole("status").filter({ hasText: /^Chargement/ })).toHaveCount(0);
  return page.getByRole("main").getByRole("listitem").filter({ hasText: text });
}

async function exportJson(page: Page): Promise<string> {
  await nav(page, "Réglages");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "JSON seul" }).click();
  const path = await (await download).path();
  return readFileSync(path, "utf8");
}

test("90-day purge: the kept message stays, the others leave the chat and the search, audited", async ({ page }) => {
  test.setTimeout(150_000);
  await enrol(page, "Navigateur E2E purge");
  await allowPersonalAi(page);
  await nav(page, "Assistant");
  await newConversation(page);
  await send(page, KEPT);
  await send(page, GONE);
  // « Conserver »: this message escapes the purge of raw conversations.
  const kept = messages(page)
    .getByRole("listitem")
    .filter({ has: page.getByText(KEPT, { exact: true }) });
  await kept.getByRole("button", { name: "Conserver", exact: true }).click();
  await expect(kept.getByText("Conservé", { exact: true })).toBeVisible();
  await expect(await search(page, "zéphyrine", "zéphyrine du jardin")).not.toHaveCount(0);

  // 90 days later: the conversation's other messages are past their date, the job runs.
  sql(`UPDATE messages SET purge_after = now() - interval '1 day'
       WHERE purge_after IS NOT NULL
         AND conversation_id = (SELECT conversation_id FROM messages WHERE content = '${GONE}' LIMIT 1)`);
  runJob("purge_messages");

  // Reloaded: nothing from the page's cache.
  await page.reload();
  await nav(page, "Assistant");
  await page.getByRole("button", { name: new RegExp(`^${KEPT}`) }).first().click();
  await expect(messages(page).getByText(KEPT, { exact: true })).toBeVisible();
  await expect(messages(page).getByText(GONE)).toHaveCount(0);
  await expect(messages(page).getByText(`[fake] ${KEPT}`)).toHaveCount(0);
  // Gone from the search too (text and semantic index).
  await expect(await search(page, "zéphyrine", "zéphyrine du jardin")).toHaveCount(0);
  // The purge is in the audit trail, with counts only (a system event: the owner's list in
  // Réglages shows the events of their account, the server's own jobs are read here).
  const [event] = sql(
    "SELECT details FROM audit_events WHERE action = 'message.purge' ORDER BY occurred_at DESC LIMIT 1",
  );
  const details = (event?.details ?? {}) as Record<string, unknown>;
  expect(Number(details.messages)).toBeGreaterThanOrEqual(3);
  expect(Object.values(details).every((value) => typeof value === "number")).toBe(true);
  const exported = await exportJson(page);
  expect(exported).toContain(KEPT);
  expect(exported).not.toContain(GONE);
});

test("UC05 erasure: a fact and a protected memory deleted leave the search and the export", async ({ page }) => {
  test.setTimeout(120_000);
  await enrol(page, "Navigateur E2E effacement");
  await nav(page, "Mémoire");
  await page.getByLabel("Énoncé").fill(FACT);
  await page.getByRole("button", { name: "Ajouter", exact: true }).click();
  const fact = page.getByRole("listitem").filter({ hasText: FACT });
  await expect(fact).toBeVisible();

  await page.getByRole("tab", { name: "Souvenirs" }).click();
  // The innermost section holding the heading: the card itself (`last`, ancestors come first).
  const form = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Nouveau souvenir" }) })
    .last();
  await form.getByLabel("Titre").fill(MEMORY.title);
  await form.getByLabel("Contenu").fill(MEMORY.content);
  await form.getByLabel("Protéger (jamais purgé automatiquement)").check();
  await form.getByRole("button", { name: "Enregistrer" }).click();
  const memory = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: MEMORY.title }) })
    .last();
  await expect(memory.getByText("Protégé", { exact: true })).toBeVisible();
  // Search snippets may cut the start of a text: matched on its distinctive part.
  await expect(await search(page, "zéphyrins", "timbres zéphyrins")).not.toHaveCount(0);

  // A protected memory asks for a confirmation before it is erased.
  await nav(page, "Mémoire");
  await page.getByRole("tab", { name: "Souvenirs" }).click();
  let confirmed = "";
  page.once("dialog", (dialog) => {
    confirmed = dialog.message();
    void dialog.accept();
  });
  await memory.getByRole("button", { name: "Supprimer", exact: true }).click();
  await expect(page.getByRole("heading", { name: MEMORY.title })).toHaveCount(0);
  expect(confirmed).toBe("Ce souvenir est protégé. Le supprimer définitivement ?");
  await page.getByRole("tab", { name: "Faits" }).click();
  await page.getByRole("listitem").filter({ hasText: FACT }).getByRole("button", { name: "Effacer" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: FACT })).toHaveCount(0);

  await expect(await search(page, "zéphyrins", "timbres zéphyrins")).toHaveCount(0);
  await expect(await search(page, "Zéphyrville", "Zéphyrville")).toHaveCount(0);
  const exported = await exportJson(page);
  expect(exported).not.toContain(FACT);
  expect(exported).not.toContain(MEMORY.content);
});
