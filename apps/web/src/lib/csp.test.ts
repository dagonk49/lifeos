import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy, newNonce } from "./csp";

function directive(policy: string, name: string): string[] {
  const found = policy
    .split(";")
    .map((part) => part.trim().split(/\s+/))
    .find(([key]) => key === name);
  if (!found) throw new Error(`no ${name} in ${policy}`);
  return found.slice(1);
}

describe("buildContentSecurityPolicy", () => {
  it("only allows scripts carrying the nonce, never inline ones", () => {
    const scripts = directive(buildContentSecurityPolicy("abc123==", { dev: false }), "script-src");
    expect(scripts).toEqual(["'self'", "'nonce-abc123=='", "'strict-dynamic'"]);
    expect(scripts).not.toContain("'unsafe-inline'");
    expect(scripts).not.toContain("'unsafe-eval'");
  });

  it("allows eval in development only", () => {
    const scripts = directive(buildContentSecurityPolicy("abc", { dev: true }), "script-src");
    expect(scripts).toContain("'unsafe-eval'");
    expect(scripts).toContain("'nonce-abc'");
    expect(scripts).not.toContain("'unsafe-inline'");
  });

  it("keeps inline styles without a nonce, which would disable them", () => {
    const styles = directive(buildContentSecurityPolicy("abc", { dev: false }), "style-src");
    expect(styles).toEqual(["'self'", "'unsafe-inline'"]);
  });

  it("keeps the same-origin limits on images, connections and framing (T14)", () => {
    const policy = buildContentSecurityPolicy("abc", { dev: false });
    expect(directive(policy, "default-src")).toEqual(["'self'"]);
    expect(directive(policy, "img-src")).toEqual(["'self'", "data:"]);
    expect(directive(policy, "connect-src")).toEqual(["'self'"]);
    expect(directive(policy, "object-src")).toEqual(["'none'"]);
    expect(directive(policy, "base-uri")).toEqual(["'self'"]);
    expect(directive(policy, "form-action")).toEqual(["'self'"]);
    expect(directive(policy, "frame-ancestors")).toEqual(["'none'"]);
  });
});

describe("newNonce", () => {
  it("is 128 random bits in base64, different every time", () => {
    const nonces = new Set(Array.from({ length: 50 }, () => newNonce()));
    expect(nonces.size).toBe(50);
    for (const nonce of nonces) {
      expect(nonce).toMatch(/^[A-Za-z0-9+/]{22}==$/);
      expect(atob(nonce)).toHaveLength(16);
    }
  });
});
