import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listenForInstallPrompt, resetInstallPromptForTests } from "@/lib/pwa";

import { InstallSection } from "./InstallSection";

const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetInstallPromptForTests();
});

describe("InstallSection", () => {
  it("offers the browser's install dialog when there is one", async () => {
    listenForInstallPrompt(window);
    render(<InstallSection />);
    expect(screen.getByRole("heading", { name: "Installer LifeOS sur le téléphone" })).toBeTruthy();
    expect(screen.getByText(/Dans le menu du navigateur/)).toBeTruthy();
    const event = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
      prompt: vi.fn(async () => undefined),
      userChoice: Promise.resolve({ outcome: "accepted" as const }),
    });
    act(() => {
      window.dispatchEvent(event);
    });
    fireEvent.click(screen.getByRole("button", { name: "Installer LifeOS" }));
    expect(event.prompt).toHaveBeenCalledOnce();
    expect(await screen.findByText("LifeOS s'installe : ouvrez-le ensuite depuis son icône.")).toBeTruthy();
  });

  it("gives the Safari steps on an iPhone", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE);
    render(<InstallSection />);
    expect(screen.getByText(/Touchez le bouton Partager, puis « Sur l'écran d'accueil »\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Installer LifeOS" })).toBeNull();
  });

  it("says when LifeOS is already open as an installed app", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(display-mode: standalone)" }));
    try {
      render(<InstallSection />);
      expect(screen.getByText("LifeOS est ouvert en application installée.")).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
