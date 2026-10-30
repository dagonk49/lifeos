import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { CalendarEvent, ConnectorAccount, RemoteCalendar, Space } from "@/lib/types";

import { AgendaDays } from "./AgendaWeek";
import { CalendarSources } from "./CalendarSources";

const spaces: Space[] = [
  { id: "perso", kind: "personal", name: "Personnel" },
  { id: "pro", kind: "professional", name: "Travail" },
];

function account(overrides: Partial<ConnectorAccount> = {}): ConnectorAccount {
  return {
    id: "a1",
    provider: "google",
    email: "agenda.demo@example.org",
    status: "active",
    capabilities: ["calendar.read"],
    grants: [{ id: "g1", space_id: "perso", capability: "calendar.read", created_at: "", expires_at: null }],
    sources: [],
    connected_at: "2026-10-20T08:00:00Z",
    revoked_at: null,
    last_error: null,
    ...overrides,
  };
}

const calendars: RemoteCalendar[] = [
  { provider_ref: "primary", name: "Moi", primary: true, read_only: false, source_id: null, space_id: null },
  { provider_ref: "family", name: "Famille", primary: false, read_only: true, source_id: null, space_id: null },
];

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrap(node: React.ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SpaceProvider spaces={spaces}>{node}</SpaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CalendarSources", () => {
  it("offers only the spaces where calendar reading was consented", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
      if (url === "/v1/connectors/accounts/a1/calendars") return json(200, calendars);
      if (url === "/v1/connectors/sources" && init.method === "POST") {
        return json(201, { id: "s1", space_id: "perso", kind: "calendar", name: "Moi", enabled: true, last_synced_at: null, last_error: null });
      }
      return json(404, { status: 404, code: "not_found", title: "Not Found" });
    });
    vi.stubGlobal("fetch", fetchMock);
    wrap(<CalendarSources account={account()} />);

    fireEvent.click(screen.getByRole("button", { name: "Choisir les agendas à suivre" }));
    const list = await screen.findByRole("list", { name: "Agendas du compte" });
    expect(within(list).getByText(/Moi/).textContent).toContain("(principal)");
    expect(within(list).getByText(/lecture seule/)).toBeTruthy();
    const select = within(list).getByLabelText("Espace pour l'agenda Moi") as HTMLSelectElement;
    // « Travail » has no calendar.read consent: not offered.
    expect([...select.options].map((o) => o.textContent)).toEqual(["Personnel"]);

    fireEvent.click(within(list).getAllByRole("button", { name: "Suivre dans cet espace" })[0]!);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url, init]) => url === "/v1/connectors/sources" && init?.method === "POST")).toBe(
        true,
      ),
    );
    const post = fetchMock.mock.calls.find(([url, init]) => url === "/v1/connectors/sources" && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ account_id: "a1", provider_ref: "primary", space_id: "perso" });
  });

  it("shows followed calendars with their freshness and why a sync stopped", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(202, {})));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    wrap(
      <CalendarSources
        account={account({
          sources: [
            {
              id: "s1",
              space_id: "perso",
              kind: "calendar",
              name: "Moi",
              enabled: true,
              last_synced_at: "2026-10-20T08:00:00Z",
              last_error: "consent_missing",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/« Moi » → Personnel · lu le/)).toBeTruthy();
    expect(screen.getByText(/non autorisée dans cet espace/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Synchroniser" }));
    expect(await screen.findByText("Synchronisation demandée.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ne plus suivre" }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("seront supprimés"));
  });

  it("renders nothing without a calendar consent nor a followed calendar", () => {
    wrap(<CalendarSources account={account({ grants: [] })} />);
    expect(screen.queryByText("Agendas suivis")).toBeNull();
  });
});

describe("AgendaDays", () => {
  const meeting: CalendarEvent = {
    id: "e1",
    space_id: "perso",
    source_id: "s1",
    source_name: "Moi",
    synced_at: "2026-10-20T08:00:00Z",
    summary: "<b>Point</b> d'équipe",
    description: null,
    location: "Salle 2",
    all_day: false,
    starts_at: "2026-10-22T09:30:00+02:00",
    ends_at: "2026-10-22T10:00:00+02:00",
    start_date: null,
    end_date: null,
    timezone: "Europe/Paris",
    status: "confirmed",
    busy: true,
    recurring: true,
    response: "needsAction",
  };

  it("lists the occurrences of each day with their source", async () => {
    const fetchMock = vi.fn(async (_url: string) => json(200, { items: [meeting], timezone: "Europe/Paris" }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<AgendaDays days={["2026-10-21", "2026-10-22"]} spaceId="perso" />);
    const day = await screen.findByRole("region", { name: "jeudi 22 octobre" });
    // Third-party text is shown as text, never as markup.
    expect(within(day).getByText("<b>Point</b> d'équipe")).toBeTruthy();
    expect(within(day).getByText("09:30 – 10:00")).toBeTruthy();
    expect(within(day).getByText("Salle 2")).toBeTruthy();
    expect(within(day).getByText("Réponse en attente")).toBeTruthy();
    expect(within(day).getByText(/Source : Moi · lu le/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "mercredi 21 octobre" }).textContent).toContain("Rien de prévu.");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/v1/calendar/events?start=2026-10-21T00%3A00%3A00&end=2026-10-23T00%3A00%3A00&space_id=perso",
    );
  });

  it("points to the settings when nothing is followed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { items: [], timezone: "Europe/Paris" })));
    wrap(<AgendaDays days={["2026-10-21"]} spaceId={null} emptyText="Aucun événement aujourd'hui." />);
    expect(await screen.findByText(/Aucun événement aujourd'hui\./)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Choisir les agendas suivis" }).getAttribute("href")).toBe(
      "/settings#connexions",
    );
  });
});
