import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConflictsList, SlotFinder, spanLabel } from "./Planning";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function wrap(node: React.ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {node}
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("planning", () => {
  it("labels spans in the owner's zone", () => {
    expect(spanLabel("2026-10-22T09:00:00+02:00", "2026-10-22T10:00:00+02:00")).toBe("jeudi 22 octobre, 09:00 – 10:00");
    expect(spanLabel("2026-10-22T23:00:00+02:00", "2026-10-23T01:00:00+02:00")).toBe(
      "jeudi 22 octobre, 23:00 – vendredi 23 octobre 01:00",
    );
  });

  it("lists conflicts, another space's event staying anonymous", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(200, {
          items: [
            {
              first: { event_id: null, label: "Occupé (autre espace)", start: "2026-10-21T14:00:00+02:00", end: "2026-10-21T15:00:00+02:00", tentative: false },
              second: { event_id: "e2", label: "Déjeuner", start: "2026-10-21T14:30:00+02:00", end: "2026-10-21T15:30:00+02:00", tentative: true },
              start: "2026-10-21T14:30:00+02:00",
              end: "2026-10-21T15:00:00+02:00",
            },
          ],
          synced_at: null,
          calendars: 2,
        }),
      ),
    );
    wrap(<ConflictsList spaceId="perso" />);
    const item = await screen.findByText(/Occupé \(autre espace\)/);
    expect(item.closest("li")?.textContent).toContain("Déjeuner (provisoire)");
    expect(item.closest("li")?.textContent).toContain("mercredi 21 octobre, 14:30 – 15:00");
  });

  it("proposes slots with their assumptions and the events around", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      json(200, {
        items: [
          {
            start: "2026-10-22T09:00:00+02:00",
            end: "2026-10-22T10:00:00+02:00",
            assumptions: ["Durée : 60 min (durée demandée).", "Marge de 10 min autour de chaque événement."],
            events_used: [
              { event_id: "e1", label: "Atelier", start: "2026-10-22T10:30:00+02:00", end: "2026-10-22T12:00:00+02:00", tentative: false },
            ],
          },
        ],
        task_title: null,
        synced_at: "2026-10-21T08:00:00Z",
        calendars: 1,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    wrap(<SlotFinder spaceId="perso" />);
    fireEvent.click(screen.getByRole("button", { name: "Proposer un créneau" }));
    const list = await screen.findByRole("list", { name: "Créneaux proposés" });
    expect(within(list).getByText("jeudi 22 octobre, 09:00 – 10:00")).toBeTruthy();
    expect(within(list).getByText("Durée : 60 min (durée demandée).")).toBeTruthy();
    expect(within(list).getByText(/Atelier/)).toBeTruthy();
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({ space_id: "perso", duration_minutes: 60 });
  });
});
