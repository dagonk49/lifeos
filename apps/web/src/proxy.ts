import { NextResponse, type NextRequest } from "next/server";

import { buildContentSecurityPolicy, newNonce } from "@/lib/csp";

/**
 * A Content Security Policy with a fresh nonce for every response (see `src/lib/csp.ts`).
 * Next.js reads the nonce from the request header set here and puts it on its scripts.
 */
export function proxy(request: NextRequest): NextResponse {
  const nonce = newNonce();
  const policy = buildContentSecurityPolicy(nonce, {
    dev: process.env.NODE_ENV === "development",
  });
  // Replaces any policy or nonce the client sent: only this one is ever rendered.
  const headers = new Headers(request.headers);
  headers.set("Content-Security-Policy", policy);
  headers.set("x-nonce", nonce);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  // Every page, including the 404 page (also served for /favicon.ico) and router prefetches:
  // a `Purpose: prefetch` header is up to the client and must not yield a page without a
  // policy. Left out: /v1 (the API, routed before this server by Traefik in production,
  // rewritten to it in development and end-to-end tests) and the build's static files.
  matcher: ["/((?!v1(?:/|$)|_next/static/|_next/image(?:/|$)).*)"],
};
