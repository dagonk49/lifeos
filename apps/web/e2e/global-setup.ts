import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { backendEnv } from "../playwright.config";
import { adminCli, enrollmentToken, OWNER_EMAIL, STACK_DIR } from "./helpers";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");
export const TOKEN_FILE = join(import.meta.dirname, ".enrollment-token");

/**
 * Dev mode: a fresh schema through the real migrations, then the owner account and its
 * enrollment link. Stack mode (scripts/test-e2e-stack.sh): the throw-away stack already has
 * its owner, created by the script; only a fresh link is written (never a downgrade there).
 */
export default function globalSetup(): void {
  if (STACK_DIR) {
    writeFileSync(TOKEN_FILE, enrollmentToken());
    return;
  }
  const env = { ...process.env, ...backendEnv };
  const run = (...args: string[]) =>
    execFileSync("uv", ["run", "--directory", backend, ...args], { env, encoding: "utf8" });
  run("alembic", "downgrade", "base");
  run("alembic", "upgrade", "head");
  const output = adminCli("create-owner", "--email", OWNER_EMAIL, "--name", "Camille");
  const token = /enroll#token=(\S+)/.exec(output)?.[1];
  if (!token) throw new Error(`no enrollment token in CLI output:\n${output}`);
  writeFileSync(TOKEN_FILE, token);
}
