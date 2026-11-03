import { useMutation, useQuery, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { createQueryClient } from "./providers";

function problem(status: number, code: string) {
  return new Response(JSON.stringify({ status, code, title: code }), {
    status,
    headers: { "Content-Type": "application/problem+json" },
  });
}

function Tasks() {
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api("/v1/tasks") });
  const add = useMutation({ mutationFn: () => api("/v1/tasks", { method: "POST", body: {} }) });
  return (
    <>
      <p>{tasks.status}</p>
      <button onClick={() => add.mutate()}>Ajouter</button>
    </>
  );
}

function renderWith(navigate: (path: string) => void) {
  const client = createQueryClient(navigate);
  render(
    <QueryClientProvider client={client}>
      <Tasks />
    </QueryClientProvider>,
  );
  return client;
}

describe("a lost session", () => {
  beforeEach(() => window.history.replaceState(null, "", "/tasks"));
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("opens the login page from any query, asked to come back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => problem(401, "unauthenticated")));
    const navigate = vi.fn();
    renderWith(navigate);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login?next=%2Ftasks"));
  });

  it("... and from any mutation", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => (calls++ === 0 ? new Response("[]", { status: 200 }) : problem(401, "unauthenticated"))),
    );
    const navigate = vi.fn();
    renderWith(navigate);
    await screen.findByText("success");
    expect(navigate).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Ajouter" })));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login?next=%2Ftasks"));
  });

  it("only on `unauthenticated`: other errors stay on the page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => problem(403, "forbidden")));
    const navigate = vi.fn();
    renderWith(navigate);
    await screen.findByText("error");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Ajouter" })));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(navigate).not.toHaveBeenCalled();
  });

  it("never from the login page itself", async () => {
    window.history.replaceState(null, "", "/login");
    vi.stubGlobal("fetch", vi.fn(async () => problem(401, "unauthenticated")));
    const navigate = vi.fn();
    renderWith(navigate);
    await screen.findByText("error");
    expect(navigate).not.toHaveBeenCalled();
  });
});
