import { afterEach, describe, expect, it, vi } from "vitest";

import { isPublicPage, loginPath, redirectToLogin, safeNext, setSigningOut } from "./session";

describe("safeNext", () => {
  it("keeps a path of this site, with its query", () => {
    expect(safeNext("/chat")).toBe("/chat");
    expect(safeNext("/tasks?space=abc#liste")).toBe("/tasks?space=abc#liste");
  });

  it("refuses anything that could leave the site", () => {
    for (const next of [
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "\\\\evil.example",
      "javascript:alert(1)",
      "chat",
      "/chat\nLocation: //evil",
      "/%5C%5Cevil.example",
      "",
      null,
      undefined,
    ]) {
      const kept = safeNext(next);
      expect(kept === "/" || kept.startsWith("/%5C"), String(next)).toBe(true);
      expect(kept.startsWith("//")).toBe(false);
    }
  });

  it("never comes back to the public pages", () => {
    expect(safeNext("/login")).toBe("/");
    expect(safeNext("/login?next=/chat")).toBe("/");
    expect(safeNext("/enroll#token=x")).toBe("/");
  });
});

describe("loginPath", () => {
  it("asks to come back to the page, not to the home page", () => {
    expect(loginPath("/chat")).toBe("/login?next=%2Fchat");
    expect(loginPath("/tasks", "?space=a")).toBe("/login?next=%2Ftasks%3Fspace%3Da");
    expect(loginPath("/")).toBe("/login");
  });

  it("knows the public pages", () => {
    expect(isPublicPage("/login")).toBe(true);
    expect(isPublicPage("/enroll")).toBe(true);
    expect(isPublicPage("/chat")).toBe(false);
    expect(isPublicPage("/loginx")).toBe(false);
  });
});

describe("redirectToLogin", () => {
  afterEach(() => {
    setSigningOut(false);
    window.history.replaceState(null, "", "/");
  });

  it("opens the login page, asked to come back", () => {
    window.history.replaceState(null, "", "/chat?c=1");
    const navigate = vi.fn();
    expect(redirectToLogin(navigate)).toBe(true);
    expect(navigate).toHaveBeenCalledWith("/login?next=%2Fchat%3Fc%3D1");
  });

  it("does nothing on a public page or while signing out", () => {
    const navigate = vi.fn();
    window.history.replaceState(null, "", "/login");
    expect(redirectToLogin(navigate)).toBe(false);
    window.history.replaceState(null, "", "/chat");
    setSigningOut(true);
    expect(redirectToLogin(navigate)).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });
});
