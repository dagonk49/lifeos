import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canRegisterServiceWorker,
  installMethod,
  listenForInstallPrompt,
  promptInstall,
  registerServiceWorker,
  resetInstallPromptForTests,
  useInstallPrompt,
} from "./pwa";

const ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1";

function env({
  secure = true,
  worker = true,
  userAgent = ANDROID,
  standalone = false,
  register = vi.fn(async () => ({})),
} = {}) {
  const navigator = {
    userAgent,
    maxTouchPoints: 5,
    ...(worker ? { serviceWorker: { register } } : {}),
  } as unknown as Navigator;
  const window = {
    isSecureContext: secure,
    matchMedia: () => ({ matches: standalone }),
  } as unknown as Window;
  return { env: { navigator, window }, register };
}

afterEach(() => resetInstallPromptForTests());

describe("service worker registration", () => {
  it("registers /sw.js for the whole site in a secure context", async () => {
    const { env: e, register } = env();
    await expect(registerServiceWorker(e)).resolves.toBe(true);
    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
  });

  it("does nothing without service workers or outside a secure context", async () => {
    for (const options of [{ secure: false }, { worker: false }]) {
      const { env: e, register } = env(options);
      expect(canRegisterServiceWorker(e)).toBe(false);
      await expect(registerServiceWorker(e)).resolves.toBe(false);
      expect(register).not.toHaveBeenCalled();
    }
    await expect(registerServiceWorker(null)).resolves.toBe(false);
  });

  it("reports a refused registration instead of throwing", async () => {
    const { env: e } = env({
      register: vi.fn(async () => {
        throw new DOMException("blocked", "SecurityError");
      }),
    });
    await expect(registerServiceWorker(e)).resolves.toBe(false);
  });
});

describe("install prompt", () => {
  function installEvent(outcome: "accepted" | "dismissed") {
    const event = new Event("beforeinstallprompt", { cancelable: true });
    return Object.assign(event, { prompt: vi.fn(async () => undefined), userChoice: Promise.resolve({ outcome }) });
  }

  it("keeps the browser's prompt for the button, and shows it once", async () => {
    const target = new EventTarget() as unknown as Window;
    listenForInstallPrompt(target);
    const { result } = renderHook(() => useInstallPrompt());
    expect(result.current.prompt).toBeNull();
    const event = installEvent("accepted");
    act(() => {
      target.dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(true);
    expect(result.current.prompt).toBe(event);
    let outcome: string | undefined;
    await act(async () => {
      outcome = await promptInstall();
    });
    expect(outcome).toBe("accepted");
    expect(event.prompt).toHaveBeenCalledOnce();
    expect(result.current.prompt).toBeNull();
    await expect(promptInstall()).resolves.toBe("unavailable");
    act(() => {
      target.dispatchEvent(new Event("appinstalled"));
    });
    expect(result.current.installed).toBe(true);
  });

  it("picks how to install from this browser", () => {
    const none = { prompt: null, installed: false };
    expect(installMethod(none, env({ standalone: true }).env)).toBe("installed");
    expect(installMethod({ prompt: null, installed: true }, env().env)).toBe("installed");
    expect(installMethod({ prompt: installEvent("dismissed"), installed: false }, env().env)).toBe("prompt");
    expect(installMethod(none, env({ userAgent: IPHONE }).env)).toBe("ios");
    expect(installMethod(none, env().env)).toBe("menu");
  });
});
