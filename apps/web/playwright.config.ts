import { tmpdir } from "node:os";
import { join } from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real backend (fake LLM, dedicated database) and the
 * production build of the web client. Requirements: PostgreSQL with a `lifeos_e2e`
 * database (extensions vector + unaccent), Redis, `uv`, and `pnpm build` done.
 */
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

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: "http://localhost:3000",
    locale: "fr-FR",
    timezoneId: "Europe/Paris",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      // The phone journeys only run on the phone.
      testIgnore: /mobile\.spec\.ts$/,
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
  webServer: [
    {
      // The API and a worker (background jobs: document reading, dictation transcription).
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
