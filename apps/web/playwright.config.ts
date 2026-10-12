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
  LIFEOS_LOG_LEVEL: "WARNING",
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
      use: {
        ...devices["Desktop Chrome"],
        // A fake microphone (a tone) for dictation, granted without a prompt.
        permissions: ["microphone"],
        launchOptions: {
          args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
          ...(process.env.PLAYWRIGHT_CHROMIUM_PATH
            ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
            : {}),
        },
      },
    },
  ],
  webServer: [
    {
      command:
        "uv run --directory ../../backend uvicorn lifeos.main:create_app --factory --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/healthz",
      env: backendEnv,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // Background jobs: document reading and dictation transcription (fakes, see above).
      // Dramatiq has no HTTP endpoint: readiness is the API's.
      command:
        "uv run --directory ../../backend dramatiq lifeos.workers.actors --processes 1 --threads 2",
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
