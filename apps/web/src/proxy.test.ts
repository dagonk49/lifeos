// @vitest-environment node
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { config, proxy } from "./proxy";

function nonceOf(policy: string | null): string | undefined {
  return /'nonce-([^']+)'/.exec(policy ?? "")?.[1];
}

function policyFor(url: string, headers?: Record<string, string>) {
  const response = proxy(new NextRequest(url, { headers }));
  return {
    sent: response.headers.get("content-security-policy"),
    // Request headers overridden for the rendering: Next.js reads the nonce from them.
    rendered: response.headers.get("x-middleware-request-content-security-policy"),
    nonceHeader: response.headers.get("x-middleware-request-x-nonce"),
  };
}

describe("proxy", () => {
  it("sends a policy with a nonce and hands the same policy to the renderer", () => {
    const { sent, rendered, nonceHeader } = policyFor("http://localhost:3000/login");
    const nonce = nonceOf(sent);
    expect(nonce).toBeDefined();
    expect(sent).toContain("'strict-dynamic'");
    expect(rendered).toBe(sent);
    expect(nonceHeader).toBe(nonce);
  });

  it("uses a new nonce for every request", () => {
    const nonces = new Set(
      Array.from({ length: 10 }, () => nonceOf(policyFor("http://localhost:3000/chat").sent)),
    );
    expect(nonces.size).toBe(10);
  });

  it("replaces a policy or a nonce sent by the client", () => {
    const { sent, rendered, nonceHeader } = policyFor("http://localhost:3000/login", {
      "content-security-policy": "script-src 'nonce-forged'",
      "x-nonce": "forged",
    });
    expect(rendered).not.toContain("forged");
    expect(nonceHeader).not.toBe("forged");
    expect(nonceOf(rendered)).toBe(nonceOf(sent));
  });

  it("does not allow inline scripts nor eval outside development", () => {
    const { sent } = policyFor("http://localhost:3000/");
    const scripts = sent?.split(";").find((part) => part.trim().startsWith("script-src"));
    expect(scripts).toBeDefined();
    expect(scripts).not.toContain("'unsafe-inline'");
    expect(scripts).not.toContain("'unsafe-eval'");
  });
});

describe("proxy matcher", () => {
  const matches = (url: string, headers?: Record<string, string>) =>
    unstable_doesMiddlewareMatch({ config, url, headers });

  it.each([
    "/",
    "/login",
    "/enroll",
    "/chat",
    "/goals?space=abc",
    "/settings",
    "/page-inexistante",
    "/v1foo",
    "/favicon.ico",
    "/icon.svg",
  ])("covers %s", (url) => {
    expect(matches(url)).toBe(true);
  });

  it("covers router prefetches: their headers are set by the client", () => {
    expect(matches("/chat", { "next-router-prefetch": "1" })).toBe(true);
    expect(matches("/chat", { purpose: "prefetch" })).toBe(true);
    expect(matches("/chat?_rsc=abc", { rsc: "1" })).toBe(true);
  });

  it.each([
    "/v1",
    "/v1/auth/me",
    "/v1/documents/abc/original",
    "/_next/static/chunks/main.js",
    "/_next/image?url=x",
  ])("leaves out %s (API or build files)", (url) => {
    expect(matches(url)).toBe(false);
  });
});
