import { execFileSync } from "node:child_process";
import { join } from "node:path";

import type { CDPSession, Page } from "@playwright/test";

import { backendEnv } from "../playwright.config";
import { expect } from "./fixtures";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");

/** The owner created by global-setup (dev mode) or by scripts/test-e2e-stack.sh. */
export const OWNER_EMAIL = "e2e@example.org";

/**
 * Production-like mode (scripts/test-e2e-stack.sh): the infra/ directory of the throw-away
 * Compose stack. The backend commands then run in its `api` container, as an operator would.
 */
export const STACK_DIR = process.env.LIFEOS_E2E_STACK ?? "";

/** Runs a command of the backend: `uv run` here, `docker compose exec api` in stack mode. */
function backendRun(args: string[], input?: string): string {
  if (STACK_DIR) {
    return execFileSync("docker", ["compose", "exec", "-T", "api", ...args], {
      cwd: STACK_DIR,
      encoding: "utf8",
      input,
    });
  }
  return execFileSync("uv", ["run", "--directory", backend, ...args], {
    env: { ...process.env, ...backendEnv },
    encoding: "utf8",
    input,
  });
}

/** `lifeos-admin …` on the server (enrolment links, revocations, `run-job`). */
export function adminCli(...args: string[]): string {
  return backendRun(["lifeos-admin", ...args]);
}

/** Python run by the backend's interpreter, with its settings (the code comes on stdin). */
export function backendPython(code: string): string {
  return backendRun(["python", "-"], code);
}

/**
 * One SQL statement on the LifeOS database (the app's role), for what no screen does:
 * backdating a deadline. Returns the rows as JSON objects.
 */
export function sql(statement: string): Record<string, unknown>[] {
  const output = backendPython(`
import asyncio, json
from sqlalchemy import text
from lifeos.config import get_settings
from lifeos.db.session import Database

async def main():
    db = Database(get_settings().database_url, pool_size=1)
    try:
        async with db.engine.begin() as conn:
            result = await conn.execute(text(${JSON.stringify(statement)}))
            rows = [dict(row._mapping) for row in result] if result.returns_rows else []
    finally:
        await db.dispose()
    print("ROWS=" + json.dumps(rows, default=str))

asyncio.run(main())
`);
  const line = output.split("\n").find((l) => l.startsWith("ROWS="));
  if (!line) throw new Error(`no result from the backend:\n${output}`);
  return JSON.parse(line.slice("ROWS=".length)) as Record<string, unknown>[];
}

/** Runs a background job now (`lifeos-admin run-job`), instead of waiting for the scheduler. */
export function runJob(name: string): void {
  adminCli("run-job", name);
}

/** A fresh enrollment link for the owner (other journeys used the previous ones). */
export function enrollmentToken(): string {
  const output = adminCli("enrollment-token", "--email", OWNER_EMAIL);
  const token = /enroll#token=(\S+)/.exec(output)?.[1];
  if (!token) throw new Error(`no enrollment token in CLI output:\n${output}`);
  return token;
}

/**
 * Chromium virtual authenticator: a platform passkey with user verification. Returns its
 * DevTools session and id (the restore journey exports and re-imports its credential).
 */
export async function addVirtualAuthenticator(page: Page): Promise<{ cdp: CDPSession; authenticatorId: string }> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return { cdp, authenticatorId };
}

/** Enrols a new passkey for this page and lands on the home page; returns its authenticator. */
export async function enrol(page: Page, device: string): Promise<{ cdp: CDPSession; authenticatorId: string }> {
  const authenticator = await addVirtualAuthenticator(page);
  await page.goto(`/enroll#token=${enrollmentToken()}`);
  await expect(page).toHaveTitle("Enregistrer cet appareil · LifeOS");
  await page.getByLabel("Nom de l'appareil").fill(device);
  await page.getByRole("button", { name: "Créer ma passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^Aujourd'hui/ })).toBeVisible();
  return authenticator;
}

export function navigation(page: Page) {
  return page.getByRole("navigation", { name: "Navigation principale" });
}

/** Opens a page from the main navigation (AppShell). */
export async function nav(page: Page, name: string): Promise<void> {
  await navigation(page).getByRole("link", { name, exact: true }).click();
}

/** The main navigation (AppShell), with the page each link opens. */
export const NAV: [label: string, path: string][] = [
  ["Accueil", "/"],
  ["Assistant", "/chat"],
  ["Tâches", "/tasks"],
  ["Journal", "/journal"],
  ["Objectifs", "/goals"],
  ["Documents", "/documents"],
  ["Finances", "/finances"],
  ["Bilans", "/reviews"],
  ["Mémoire", "/memory"],
  ["Recherche", "/search"],
  ["Approbations", "/approvals"],
  ["Réglages", "/settings"],
];

/**
 * Every page of the client with its HTTP status: public ones, those of a signed-in owner
 * (the HTML is the same without a session, the API checks it) and the 404 page.
 */
export const LOGIN = { path: "/login", status: 200 };
export const ROUTES: { path: string; status: number }[] = [
  LOGIN,
  { path: "/enroll", status: 200 },
  ...["/", "/chat", "/tasks", "/agenda", "/mail", "/journal", "/goals", "/documents", "/finances", "/reviews"]
    .concat(["/memory", "/search", "/approvals", "/settings", "/connexions/retour"])
    .map((path) => ({ path, status: 200 })),
  { path: "/page-inexistante", status: 404 },
];

/** The messages of the open conversation (Assistant). */
export function messages(page: Page) {
  return page.getByRole("list", { name: "Messages" });
}

/** Opens a new conversation and waits for it to exist (sending sooner would open two). */
export async function newConversation(page: Page): Promise<void> {
  const button = page.getByRole("button", { name: "Nouvelle conversation", exact: true });
  const created = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/chat/conversations" && response.request().method() === "POST",
  );
  await button.click();
  await created;
  await expect(button).toBeEnabled();
}

/** Allows the AI in the personal space from Réglages (another journey may have done it). */
export async function allowPersonalAi(page: Page): Promise<void> {
  await nav(page, "Réglages");
  const personal = page.getByRole("listitem").filter({ hasText: "Personnel" }).filter({ hasText: "IA" });
  const allow = personal.getByRole("button", { name: "Autoriser l'IA" });
  await expect(allow.or(personal.getByText("IA autorisée"))).toBeVisible();
  if (await allow.isVisible()) await allow.click();
  await expect(personal.getByText("IA autorisée")).toBeVisible();
}
