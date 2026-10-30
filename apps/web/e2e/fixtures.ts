import { test as base, expect } from "@playwright/test";

/** A Content Security Policy violation reported by the page (`securitypolicyviolation`). */
export interface CspViolation {
  directive: string;
  blocked: string;
  page: string;
}

/**
 * Every journey fails on a Content Security Policy violation (src/proxy.ts): a framework
 * script blocked for a missing nonce would be a broken page in production. A test that
 * injects content on purpose checks `cspViolations` itself, then empties it.
 */
export const test = base.extend<{ cspViolations: CspViolation[] }>({
  cspViolations: [
    async ({ page }, use) => {
      const violations: CspViolation[] = [];
      // Bindings and init scripts are added through the DevTools protocol, outside the CSP,
      // and survive navigations.
      await page.exposeBinding("__lifeosCspViolation", (_source, violation: CspViolation) => {
        violations.push(violation);
      });
      await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (event) => {
          const report = (window as unknown as { __lifeosCspViolation?: (v: CspViolation) => void })
            .__lifeosCspViolation;
          report?.({ directive: event.effectiveDirective, blocked: event.blockedURI, page: location.pathname });
        });
      });
      await use(violations);
      expect(violations, "violations de la Content Security Policy").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
