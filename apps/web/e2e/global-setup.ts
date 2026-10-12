import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import { backendEnv } from "../playwright.config";

const backend = join(import.meta.dirname, "..", "..", "..", "backend");
export const TOKEN_FILE = join(import.meta.dirname, ".enrollment-token");

/** Fresh schema through the real migrations, then the owner account and its enrollment link. */
export default function globalSetup(): void {
  const env = { ...process.env, ...backendEnv };
  const run = (...args: string[]) =>
    execFileSync("uv", ["run", "--directory", backend, ...args], { env, encoding: "utf8" });
  run("alembic", "downgrade", "base");
  run("alembic", "upgrade", "head");
  const output = run("lifeos-admin", "create-owner", "--email", "e2e@example.org", "--name", "Camille");
  const token = /enroll#token=(\S+)/.exec(output)?.[1];
  if (!token) throw new Error(`no enrollment token in CLI output:\n${output}`);
  writeFileSync(TOKEN_FILE, token);
}
