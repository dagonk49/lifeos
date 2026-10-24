import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EraseConfirm } from "./EraseConfirm";

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderConfirm(props: { derivedPath?: string; onDone?: (withDerived: boolean) => void }) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <EraseConfirm
        question="Supprimer l'entrée du 12 octobre ?"
        confirmLabel="Supprimer l'entrée"
        deletePath="/v1/journal/j1"
        derivedPath={props.derivedPath}
        onDone={props.onDone ?? (() => {})}
        onCancel={() => {}}
      />
    </QueryClientProvider>,
  );
}

const derived = {
  items: [
    { type: "fact", id: "f1", text: "Vacances en Bretagne en juillet" },
    { type: "task", id: "t1", text: "Réserver le train" },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EraseConfirm", () => {
  it("lists what was saved from the entry and keeps it by default", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) =>
      init?.method === "DELETE" ? json(204, null) : json(200, derived),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onDone = vi.fn();
    renderConfirm({ derivedPath: "/v1/journal/j1/derived", onDone });

    const list = await screen.findByRole("list", { name: "Ce qui en a été tiré" });
    expect(list.textContent).toContain("Vacances en Bretagne en juillet");
    expect(list.textContent).toContain("Réserver le train");
    const box = screen.getByLabelText("Supprimer aussi ce qui en a été tiré (2)") as HTMLInputElement;
    expect(box.checked).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Supprimer l'entrée" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(false));
    const deleted = fetchMock.mock.calls.find((c) => c[1]?.method === "DELETE");
    expect(deleted?.[0]).toBe("/v1/journal/j1");
  });

  it("deletes what was saved too when the owner ticks it", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) =>
      init?.method === "DELETE" ? json(204, null) : json(200, derived),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onDone = vi.fn();
    renderConfirm({ derivedPath: "/v1/journal/j1/derived", onDone });

    fireEvent.click(await screen.findByLabelText("Supprimer aussi ce qui en a été tiré (2)"));
    fireEvent.click(screen.getByRole("button", { name: "Supprimer l'entrée" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(true));
    const deleted = fetchMock.mock.calls.find((c) => c[1]?.method === "DELETE");
    expect(deleted?.[0]).toBe("/v1/journal/j1?with_derived=true");
  });

  it("says when nothing was saved from it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { items: [] })));
    renderConfirm({ derivedPath: "/v1/journal/j1/derived" });
    expect(await screen.findByText(/Aucun fait, aucune tâche ni aucun souvenir/)).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
