import { tmpdir } from "node:os";
import { join } from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real backend (fake LLM, dedicated database) and the
 * production build of the web client. Requirements: PostgreSQL with a `lifeos_e2e`
 * database (extensions vector + unaccent), Redis, `uv`, and `pnpm build` done.
 *
 * Production-like mode (`stack` project): LIFEOS_E2E_BASE_URL points to a throw-away
 * LifeOS started by scripts/test-e2e-stack.sh (Docker images, Traefik, TLS from a local
 * authority, secure cookies, LIFEOS_ENVIRONMENT=production, scheduler); no server is
 * started here and the backend commands run in its containers (e2e/helpers.ts).
 */
const stackUrl = process.env.LIFEOS_E2E_BASE_URL;
// A name per run (the stack's two phases): its results and report are kept side by side.
const run = process.env.LIFEOS_E2E_RUN;
const outputDir = run ? `test-results/${run}` : "test-results";
const backendEnv = {
  LIFEOS_ENVIRONMENT: "development",
  LIFEOS_DATABASE_NAME: process.env.LIFEOS_E2E_DATABASE ?? "lifeos_e2e",
  LIFEOS_DATABASE_USER: process.env.LIFEOS_DATABASE_USER ?? "lifeos",
  LIFEOS_DATABASE_PASSWORD: process.env.LIFEOS_DATABASE_PASSWORD ?? "lifeos",
  LIFEOS_REDIS_URL: process.env.LIFEOS_REDIS_URL ?? "redis://localhost:6379/13",
  LIFEOS_PUBLIC_ORIGIN: "http://localhost:3000",
  LIFEOS_WEBAUTHN_RP_ID: "localhost",
  LIFEOS_COOKIE_SECURE: "false",
  LIFEOS_LLM_PROVIDER: "fake",
  // Exercises the vector search path (pgvector) with a deterministic local embedder.
  LIFEOS_EMBEDDINGS_PROVIDER: "fake",
  // Documents (step 4): encrypted storage with a throw-away key; text files are read for
  // real, pictures and recordings by deterministic fakes (no OCR or speech model in E2E).
  LIFEOS_DOCUMENTS_DIR: join(tmpdir(), "lifeos-e2e-documents"),
  LIFEOS_DOCUMENTS_KEY: "ZTJlLW9ubHktZG9jdW1lbnRzLWtleS0zMi1ieXRlcyE=",
  LIFEOS_EXTRACTION_PROVIDER: "fake",
  LIFEOS_STT_PROVIDER: "fake",
  // Connected accounts (step 5): the in-memory fake provider ("Démo") agrees at once and
  // sends the browser back to /connexions/retour; tokens sealed with a throw-away key.
  LIFEOS_CONNECTORS_PROVIDER: "fake",
  LIFEOS_CONNECTORS_KEY: "ZTJlLW9ubHktY29ubmVjdG9ycy1rZXktMzJieXRlcyE=",
  // Web Push (task 19): devices are registered for real, the worker's fake sender sends
  // nothing (the browser's subscription is stubbed in e2e/push.spec.ts).
  LIFEOS_PUSH_PROVIDER: "fake",
  LIFEOS_LOG_LEVEL: "WARNING",
};

const chromiumOptions = {
  // A fake microphone (a tone) for dictation, granted without a prompt.
  permissions: ["microphone"],
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    ...(process.env.PLAYWRIGHT_CHROMIUM_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } : {}),
  },
};

/**
 * The journeys run on the production-like stack: the priority ones (§9 criteria, UC01–UC05,
 * session loss, AI outage, purge, reminders), then the restore (two phases, see
 * e2e/stack/restore.spec.ts). Files run in alphabetical order on one database.
 */
const STACK_JOURNEYS =
  /(^|\/)(mvp|notifications|outage|purge|security|session|spaces-agents|stack\/restore)\.spec\.ts$/;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // In the CI, the proof of every run is kept: an HTML report and a JUnit file (uploaded as
  // artifacts, summed up in the job summary), and a screenshot of each journey's last screen.
  reporter: process.env.CI
    ? [
        ["github"],
        ["list"],
        ["html", { open: "never", outputFolder: run ? `playwright-report/${run}` : "playwright-report" }],
        ["junit", { outputFile: `${outputDir}/junit.xml` }],
      ]
    : "list",
  globalSetup: "./e2e/global-setup.ts",
  outputDir,
  use: {
    baseURL: stackUrl ?? "http://localhost:3000",
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
    trace: "retain-on-failure",
    screenshot: process.env.CI ? "on" : "only-on-failure",
  },
  projects: stackUrl
    ? [
        {
          name: "stack",
          testMatch: STACK_JOURNEYS,
          use: {
            ...devices["Desktop Chrome"],
            ...chromiumOptions,
            launchOptions: {
              ...chromiumOptions.launchOptions,
              args: [
                ...chromiumOptions.launchOptions.args,
                // The test domain resolves to Traefik. Its certificate comes from a local
                // authority trusted in the NSS database (WebAuthn refuses certificate errors).
                `--host-resolver-rules=MAP ${new URL(stackUrl).hostname} ${process.env.LIFEOS_E2E_STACK_ADDRESS ?? "127.0.0.1"}`,
                // Straight to Traefik, whatever proxy the machine sets (HTTPS_PROXY).
                "--no-proxy-server",
              ],
            },
          },
        },
      ]
    : [
        {
          name: "chromium",
          // The phone journeys only run on the phone; the restore needs the Compose stack.
          testIgnore: [/mobile\.spec\.ts$/, /stack\//],
          use: { ...devices["Desktop Chrome"], ...chromiumOptions },
        },
        {
          // A phone (Chromium: the CDP virtual authenticator does not exist in WebKit, so iOS
          // is covered by the manual protocol of docs/accessibilite.md). Touch screen, coarse
          // pointer, 412 px wide; mobile.spec.ts also checks 360 and 375 px.
          name: "mobile-chromium",
          testMatch: /(mobile|a11y)\.spec\.ts$/,
          use: { ...devices["Pixel 7"], ...chromiumOptions },
        },
      ],
  webServer: stackUrl
    ? undefined
    : [
        {
          // The API, a worker (background jobs) and the scheduler, as in production.
          command: "bash e2e/backend.sh",
          url: "http://127.0.0.1:8000/healthz",
          env: backendEnv,
          reuseExistingServer: false,
          timeout: 60_000,
        },
        {
          command: "pnpm start",
          url: "http://localhost:3000/login",
          env: { NEXT_TELEMETRY_DISABLED: "1", PORT: "3000" },
          reuseExistingServer: false,
          timeout: 60_000,
        },
      ],
});

export { backendEnv };
