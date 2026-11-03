import { enrol, nav } from "./a11y-helpers";
import { expect, test } from "./fixtures";

/** Keyboard only (WCAG 2.1.1, 2.4.1, 2.4.3, 2.4.7): no mouse click in this journey. */

// Synthetic IBAN (valid key, used by the backend tests): masked before sending.
const IBAN = "FR76 3000 6000 0112 3456 7890 189";

test("keyboard only: skip link, navigation, notifications, tabs, composer, send review", async ({ page }) => {
  test.setTimeout(120_000);
  await enrol(page, "Clavier E2E");

  // The banner and the navigation keep their names and structure.
  await expect(page.getByRole("banner")).toMatchAriaSnapshot(`
    - banner:
      - link "LifeOS"
      - combobox "Espace"
      - button /^Notifications/
      - button "Déconnexion"
      - navigation "Navigation principale":
        - list:
          - listitem:
            - link "Accueil"
          - listitem:
            - link "Assistant"
          - listitem:
            - link "Tâches"
          - listitem:
            - link "Agenda"
          - listitem:
            - link "E-mails"
          - listitem:
            - link "Journal"
          - listitem:
            - link "Objectifs"
          - listitem:
            - link "Documents"
          - listitem:
            - link "Finances"
          - listitem:
            - link "Bilans"
          - listitem:
            - link "Mémoire"
          - listitem:
            - link "Recherche"
          - listitem:
            - link "Approbations"
          - listitem:
            - link "Réglages"
  `);

  // The skip link is the first stop and moves the focus to the content.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Aller au contenu" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("main#contenu")).toBeFocused();

  // The navigation, by keyboard: the new page's title is what the route announcer reads.
  const tasks = page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Tâches" });
  await tasks.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page).toHaveTitle("Tâches · LifeOS");
  await expect(tasks).toHaveAttribute("aria-current", "page");

  // Notifications: Enter opens, Escape closes and gives the focus back.
  const bell = page.getByRole("button", { name: /^Notifications/ });
  await bell.focus();
  await page.keyboard.press("Enter");
  await expect(bell).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("group", { name: "Notifications" })).toBeVisible();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Escape");
  await expect(bell).toHaveAttribute("aria-expanded", "false");
  await expect(bell).toBeFocused();

  // Memory tabs: arrows, Home and End.
  await nav(page, "Mémoire");
  const facts = page.getByRole("tab", { name: "Faits" });
  await facts.focus();
  await page.keyboard.press("ArrowRight");
  const memories = page.getByRole("tab", { name: "Souvenirs" });
  await expect(memories).toBeFocused();
  await expect(memories).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel", { name: "Souvenirs" })).toBeVisible();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /^À vérifier/ })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(facts).toBeFocused();
  await expect(facts).toHaveAttribute("aria-selected", "true");

  // The composer: Shift+Enter goes to the next line, Enter sends (desktop).
  await nav(page, "Assistant");
  const box = page.getByLabel("Votre message");
  await box.focus();
  await page.keyboard.type("Bonjour");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("au clavier");
  await expect(box).toHaveValue("Bonjour\nau clavier");
  const sent = page.waitForResponse((r) => r.url().endsWith("/messages") && r.request().method() === "POST");
  await page.keyboard.press("Enter");
  await sent;
  await expect(page.getByRole("list", { name: "Messages" }).getByText(/au clavier/)).toBeVisible();

  // The control before sending takes the focus; Escape cancels it and returns to the message.
  await box.focus();
  await page.keyboard.type(`Virement sur ${IBAN}`);
  await page.keyboard.press("Enter");
  const review = page.getByRole("region", { name: "Avant l'envoi à l'assistant IA" });
  await expect(review).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(review).toHaveCount(0);
  await expect(box).toBeFocused();
  await expect(box).toHaveValue(`Virement sur ${IBAN}`);
});
