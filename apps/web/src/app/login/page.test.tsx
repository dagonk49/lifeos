import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./page";

const replace = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useSearchParams: () => search,
}));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

async function signIn(query: string) {
  search = new URLSearchParams(query);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <LoginPage />
    </QueryClientProvider>,
  );
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Se connecter avec une passkey" })));
}

describe("login page", () => {
  afterEach(() => {
    cleanup();
    replace.mockReset();
  });

  it("goes back to the page of the lost session, and says why", async () => {
    await signIn("next=%2Fchat");
    expect(screen.getByText(/Session expirée : reconnectez-vous/)).toBeTruthy();
    expect(replace).toHaveBeenCalledWith("/chat");
  });

  it("goes to the home page without `next`, with no message", async () => {
    await signIn("");
    expect(screen.queryByText(/Session expirée/)).toBeNull();
    expect(replace).toHaveBeenCalledWith("/");
  });

  it.each(["https://evil.example", "//evil.example", "/\\evil.example", "/login"])(
    "never follows next=%s",
    async (next) => {
      await signIn(`next=${encodeURIComponent(next)}`);
      expect(replace).toHaveBeenCalledWith("/");
    },
  );
});
