/**
 * Content Security Policy of the web client, built for every response by `src/proxy.ts`.
 *
 * Scripts run only if they carry the nonce of the response: Next.js reads it from the
 * request's `Content-Security-Policy` header while rendering and puts it on its own scripts
 * (framework, page chunks, inline hydration data). An injected `<script>` or inline event
 * handler has no nonce and is blocked. Every page is therefore rendered per request (root
 * layout): an HTML page prerendered at build time could not carry the nonce.
 */

/** A fresh nonce for one response: 128 random bits, base64-encoded. */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}

export function buildContentSecurityPolicy(nonce: string, { dev }: { dev: boolean }): string {
  return [
    "default-src 'self'",
    // 'strict-dynamic' lets the trusted scripts load the page chunks; browsers that support it
    // then ignore 'self', kept for older ones. No 'unsafe-inline': an inline script runs only
    // with the nonce. React needs eval for its development tooling only.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    // Styles keep 'unsafe-inline' and get no nonce, on purpose: a nonce would disable
    // 'unsafe-inline', yet `style="…"` attributes cannot carry one, and Next.js renders them
    // in its built-in not-found and global-error pages (as React does for any `style` prop
    // rendered on the server). An injected style cannot run code, nor send data to another
    // site: images, fonts and connections stay limited to this origin.
    "style-src 'self' 'unsafe-inline'",
    // No remote images, scripts or connections: assistant output cannot exfiltrate data
    // through injected links or images (threat model T14).
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}
