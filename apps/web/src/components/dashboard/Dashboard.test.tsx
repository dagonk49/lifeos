import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CARD_IDS, DEFAULT_LAYOUT } from "@/lib/dashboard";
import type { AgendaItem, DashboardWeek } from "@/lib/types";

import { DashboardCustomize } from "./DashboardCustomize";
import { SuggestionsList } from "./SuggestionsList";
import { WeekView } from "./WeekView";

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

function line(partial: Partial<AgendaItem>): AgendaItem {
  return {
    kind: "task",
    title: "Signer",
    day: "2026-10-25",
    start: "2026-10-25T23:30:00+01:00",
    end: null,
    all_day: false,
    space_id: "s1",
    source_type: "task",
    source_id: "t1",
    source_label: "Tâche",
    href: "/tasks",
    synced_at: null,
    detail: null,
    tentative: false,
    blocked: false,
    ...partial,
  };
}

function week(start: string, items: Record<string, AgendaItem[]>): DashboardWeek {
  const days = Array.from({ length: 7 }, (_, i) => {
    const date = new Date(Date.UTC(Number(start.slice(0, 4)), Number(start.slice(5, 7)) - 1, Number(start.slice(8, 10)) + i))
      .toISOString()
      .slice(0, 10);
    return { date, items: items[date] ?? [] };
  });
  return { start, end: days[6]?.date ?? start, timezone: "Europe/Paris", days, calendars: 0, synced_at: null };
}

describe("week view", () => {
  it("shows each item on its day, the other space anonymous, and reads another week", async () => {
    const initial = week("2026-10-19", {
      "2026-10-25": [line({})],
      "2026-10-21": [line({ kind: "busy", title: "Occupé (autre espace)", href: null, source_id: null, start: "2026-10-21T14:00:00+02:00", end: "2026-10-21T15:00:00+02:00" })],
    });
    const fetchMock = vi.fn(async (_url: string) =>
      json(200, week("2026-10-26", { "2026-10-27": [line({ title: "Relancer", day: "2026-10-27", start: "2026-10-27T09:00:00+01:00" })] })),
    );
    vi.stubGlobal("fetch", fetchMock);
    wrap(<WeekView initial={initial} spaceId="s1" today="2026-10-21" />);

    const sunday = screen.getByRole("region", { name: "dimanche 25 octobre" });
    expect(within(sunday).getByRole("link", { name: "Signer" }).getAttribute("href")).toBe("/tasks");
    expect(within(sunday).getByText("Échéance 23:30")).toBeTruthy();
    const wednesday = screen.getByRole("region", { name: "mercredi 21 octobre" });
    expect(within(wednesday).getByText("Occupé (autre espace)")).toBeTruthy();
    expect(within(wednesday).queryByRole("link")).toBeNull();
    expect(screen.getByRole("link", { name: "Connecter un agenda" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled(); // the current week comes with the dashboard

    fireEvent.click(screen.getByRole("button", { name: "Semaine suivante" }));
    expect(await screen.findByRole("link", { name: "Relancer" })).toBeTruthy();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/v1/dashboard/week?start=2026-10-26&space_id=s1");
    expect(screen.getByRole("heading", { name: /Semaine du 26 octobre au 1 novembre 2026/ })).toBeTruthy();
  });
});

describe("customize", () => {
  it("hides a card, moves another with buttons and saves the whole layout", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => json(200, JSON.parse(String(init?.body))));
    vi.stubGlobal("fetch", fetchMock);
    const done = vi.fn();
    wrap(<DashboardCustomize layout={DEFAULT_LAYOUT} onDone={done} />);

    fireEvent.click(screen.getByRole("checkbox", { name: "Afficher « Budget »" }));
    expect((screen.getByRole("button", { name: "Monter « Priorités du jour »" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Monter « Agenda du jour »" }));
    fireEvent.click(screen.getByRole("radio", { name: "aujourd'hui" }));
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer la disposition" }));

    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/v1/dashboard/layout");
    expect(init?.method).toBe("PUT");
    const body = JSON.parse(String(init?.body)) as { cards: { id: string; visible: boolean }[]; week_start: string };
    expect(body.cards.map((c) => c.id).slice(0, 2)).toEqual(["agenda", "priorities"]);
    expect(body.cards).toHaveLength(CARD_IDS.length);
    expect(body.cards.find((c) => c.id === "budget")?.visible).toBe(false);
    expect(body.week_start).toBe("today");
  });

  it("restores the default order", () => {
    wrap(
      <DashboardCustomize
        layout={{ ...DEFAULT_LAYOUT, cards: [...DEFAULT_LAYOUT.cards].reverse().map((c) => ({ ...c, visible: false })) }}
        onDone={() => {}}
      />,
    );
    const list = screen.getByRole("list", { name: "Cartes de l'accueil" });
    expect(within(list).getAllByRole("listitem")[0]?.textContent).toContain("Assistant IA");
    fireEvent.click(screen.getByRole("button", { name: "Rétablir l'ordre par défaut" }));
    expect(within(list).getAllByRole("listitem")[0]?.textContent).toContain("Priorités du jour");
    expect((screen.getByRole("checkbox", { name: "Afficher « Budget »" }) as HTMLInputElement).checked).toBe(true);
  });
});

describe("suggestions", () => {
  it("shows why and a link to the source with its date", () => {
    wrap(
      <SuggestionsList
        items={[
          {
            text: "Bloquer deux matinées",
            why: "Trois réunions ont débordé",
            source_type: "review",
            source_id: "r1",
            source_label: "Bilan hebdomadaire du 19/10/2026",
            date: "2026-10-26T08:00:00Z",
            href: "/reviews?review=r1",
            space_id: "s1",
          },
        ]}
      />,
    );
    expect(screen.getByText("Pourquoi : Trois réunions ont débordé")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Bilan hebdomadaire du 19/10/2026" }).getAttribute("href")).toBe("/reviews?review=r1");
  });
});
