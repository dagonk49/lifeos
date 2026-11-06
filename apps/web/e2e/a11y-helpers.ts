import type { Page } from "@playwright/test";

import { expect } from "./fixtures";
import { nav } from "./helpers";

// Shared with the other journeys (e2e/helpers.ts).
export { addVirtualAuthenticator, enrol, enrollmentToken, nav } from "./helpers";

/** Every page of the main navigation (AppShell NAV), with its document title. */
export const PAGES: { path: string; link: string; title: string }[] = [
  { path: "/", link: "Accueil", title: "Accueil · LifeOS" },
  { path: "/chat", link: "Assistant", title: "Assistant · LifeOS" },
  { path: "/tasks", link: "Tâches", title: "Tâches · LifeOS" },
  { path: "/agenda", link: "Agenda", title: "Agenda · LifeOS" },
  { path: "/mail", link: "E-mails", title: "E-mails · LifeOS" },
  { path: "/journal", link: "Journal", title: "Journal · LifeOS" },
  { path: "/goals", link: "Objectifs", title: "Objectifs · LifeOS" },
  { path: "/documents", link: "Documents", title: "Documents · LifeOS" },
  { path: "/finances", link: "Finances", title: "Finances · LifeOS" },
  { path: "/reviews", link: "Bilans", title: "Bilans · LifeOS" },
  { path: "/memory", link: "Mémoire", title: "Mémoire · LifeOS" },
  { path: "/search", link: "Recherche", title: "Recherche · LifeOS" },
  { path: "/approvals", link: "Approbations", title: "Approbations · LifeOS" },
  { path: "/settings", link: "Réglages", title: "Réglages · LifeOS" },
];

export function pageAt(path: string): (typeof PAGES)[number] {
  const found = PAGES.find((p) => p.path === path);
  if (!found) throw new Error(`no page ${path} in PAGES`);
  return found;
}

/**
 * Deletes the owner's tasks with this title, from the page (same-origin, signed in). The
 * specs share one database: a task left open would change what the other journeys see.
 */
export async function deleteTasksTitled(page: Page, title: string): Promise<void> {
  await page.evaluate(async (wanted) => {
    const response = await fetch("/v1/tasks?limit=200", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const { items } = (await response.json()) as { items: { id: string; title: string }[] };
    for (const task of items.filter((t) => t.title === wanted)) {
      await fetch(`/v1/tasks/${task.id}`, { method: "DELETE", headers: { Accept: "application/json" } });
    }
  }, title);
}

/** Deletes the owner's journal entries with exactly this text (other journeys expect one
 * entry of theirs only), and the reviews whose ids are given. */
export async function deleteJournalAndReviews(page: Page, content: string, reviewIds: string[]): Promise<void> {
  await page.evaluate(
    async ({ wanted, reviews }) => {
      const headers = { Accept: "application/json" };
      const response = await fetch("/v1/journal?limit=200", { headers });
      if (response.ok) {
        const { items } = (await response.json()) as { items: { id: string; content: string }[] };
        for (const entry of items.filter((e) => e.content === wanted)) {
          await fetch(`/v1/journal/${entry.id}`, { method: "DELETE", headers });
        }
      }
      for (const id of reviews) await fetch(`/v1/reviews/${id}`, { method: "DELETE", headers });
    },
    { wanted: content, reviews: reviewIds },
  );
}

/** Opens a page from the navigation and waits for its heading and title. */
export async function openPage(page: Page, target: (typeof PAGES)[number]): Promise<void> {
  await nav(page, target.link);
  await expect(page).toHaveURL(new RegExp(`${target.path === "/" ? "/" : target.path}(\\?.*)?$`));
  await expect(page).toHaveTitle(target.title);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // Data-driven sections render after their first request.
  await expect(page.getByRole("status").filter({ hasText: /^Chargement/ })).toHaveCount(0);
}
