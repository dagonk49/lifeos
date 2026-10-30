import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resetInstallPromptForTests } from "@/lib/pwa";

import { ServiceWorkerRegistration } from "./ServiceWorkerRegistration";

function stubWorker(secure: boolean) {
  const register = vi.fn(async () => ({}));
  vi.stubGlobal("isSecureContext", secure);
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { register } });
  return register;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "serviceWorker");
  resetInstallPromptForTests();
});

describe("ServiceWorkerRegistration", () => {
  it("registers the worker of /sw.js in a secure context and renders nothing", async () => {
    const register = stubWorker(true);
    const { container } = render(<ServiceWorkerRegistration />);
    expect(container.innerHTML).toBe("");
    await vi.waitFor(() => expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" }));
  });

  it("does nothing outside a secure context", async () => {
    const register = stubWorker(false);
    render(<ServiceWorkerRegistration />);
    await Promise.resolve();
    expect(register).not.toHaveBeenCalled();
  });

  it("does nothing without service workers", () => {
    vi.stubGlobal("isSecureContext", true);
    expect(() => render(<ServiceWorkerRegistration />)).not.toThrow();
  });
});
