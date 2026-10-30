import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ChatPage from "@/app/(app)/chat/page";
import { enterSends } from "@/lib/composer";
import { SpaceProvider } from "@/lib/space";

vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A touch screen (coarse pointer) or a mouse, as `matchMedia("(pointer: coarse)")` reports. */
function pointer(coarse: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query === "(pointer: coarse)" ? coarse : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

function mockApi(): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      if (url.startsWith("/v1/chat/conversations")) return json({ items: [], next_cursor: null });
      if (url.startsWith("/v1/agents?")) return json([]);
      if (url === "/v1/chat/preview") return json({ masked: {}, attachments: [] });
      return json({ title: "Not found", status: 404, code: "not_found" }, 404);
    }),
  );
  return urls;
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Personnel" }]}>
        <ChatPage />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("enterSends", () => {
  it("sends on Enter with a mouse, never with Shift, on a touch screen or while composing", () => {
    expect(enterSends({ key: "Enter", shiftKey: false }, false)).toBe(true);
    expect(enterSends({ key: "Enter", shiftKey: true }, false)).toBe(false);
    expect(enterSends({ key: "Enter", shiftKey: false }, true)).toBe(false);
    expect(enterSends({ key: "Enter", shiftKey: false, isComposing: true }, false)).toBe(false);
    expect(enterSends({ key: "a", shiftKey: false }, false)).toBe(false);
  });
});

describe("Assistant composer", () => {
  it("sends on Enter on a desktop, and says so", async () => {
    pointer(false);
    const urls = mockApi();
    renderPage();
    const box = screen.getByLabelText("Votre message");
    const hint = document.getElementById(box.getAttribute("aria-describedby") ?? "");
    expect(hint?.textContent).toBe("Entrée envoie le message, Maj+Entrée va à la ligne.");
    fireEvent.change(box, { target: { value: "Bonjour" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(urls).toContain("/v1/chat/preview"));
  });

  it("keeps Enter for a new line on a touch screen: only the button sends", async () => {
    pointer(true);
    const urls = mockApi();
    renderPage();
    const box = screen.getByLabelText("Votre message");
    await waitFor(() =>
      expect(document.getElementById(box.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
        "Entrée va à la ligne ; le bouton « Envoyer » envoie le message.",
      ),
    );
    fireEvent.change(box, { target: { value: "Bonjour" } });
    // Not prevented: the browser inserts the new line.
    expect(fireEvent.keyDown(box, { key: "Enter" })).toBe(true);
    expect(urls).not.toContain("/v1/chat/preview");
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await waitFor(() => expect(urls).toContain("/v1/chat/preview"));
  });

  it("folds the conversation list away on a phone behind a disclosure button", () => {
    pointer(true);
    mockApi();
    renderPage();
    const toggle = screen.getByRole("button", { name: "Afficher les conversations" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).not.toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Masquer les conversations" }).getAttribute("aria-expanded")).toBe("true");
  });
});
