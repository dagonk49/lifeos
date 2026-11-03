import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Approval, CalendarEvent } from "@/lib/types";

import { ApprovalReview } from "./ApprovalReview";
import { BlockSlotButton, EventActions, parseGuests } from "./CalendarChange";

const passkey = vi.hoisted(() => ({ login: vi.fn(async () => ({})) }));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: passkey.login }));

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

const shared: Approval = {
  id: "ap1",
  space_id: "s1",
  action: "calendar.update_shared_event",
  description: "Modifier un événement partagé",
  params: {},
  preview: {
    operation: "move",
    calendar: "Agenda de démonstration",
    summary: "Point d'équipe",
    summary_line: "Déplacer « Point d'équipe »",
    timezone: "Europe/Paris",
    before: "mardi 27 octobre 2026, 09:30 – 10:00 (Europe/Paris)",
    after: "mardi 27 octobre 2026, 11:00 – 11:30 (Europe/Paris)",
    attendees: ["alex@example.org", "sam@example.org"],
    notify: false,
    shared: true,
    recurring: false,
    notes: ["Les participants ne seront pas prévenus."],
  },
  level: "reinforced",
  status: "pending",
  requested_by: "owner",
  created_at: "2026-10-26T08:00:00Z",
  expires_at: "2026-11-02T08:00:00Z",
  decided_at: null,
  executed_at: null,
  result: null,
  error: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  passkey.login.mockClear();
});

describe("calendar approvals", () => {
  it("shows the exact change: local times with the zone, attendees, notification", () => {
    wrap(<ApprovalReview approval={shared} />);
    expect(screen.getByText("Événement partagé — confirmation par passkey")).toBeTruthy();
    expect(screen.getByText("mardi 27 octobre 2026, 09:30 – 10:00 (Europe/Paris)")).toBeTruthy();
    expect(screen.getByText("mardi 27 octobre 2026, 11:00 – 11:30 (Europe/Paris)")).toBeTruthy();
    const attendees = screen.getByRole("list", { name: "Participants" });
    expect(within(attendees).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "alex@example.org",
      "sam@example.org",
    ]);
    expect(screen.getByText("Non, personne n'est prévenu")).toBeTruthy();
    // Never the raw JSON of the preview.
    expect(screen.queryByText(/"operation"/)).toBeNull();
  });

  it("asks for the passkey on a reinforced approval, then approves once", async () => {
    let release: (value: Response) => void = () => undefined;
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(new Promise<Response>((resolve) => (release = resolve)))
      .mockResolvedValueOnce(json(200, { ...shared, status: "executed" }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ApprovalReview approval={shared} />);
    const button = screen.getByRole("button", { name: "Approuver avec ma passkey" });
    fireEvent.click(button);
    // While the decision runs, no second click (nor a rejection) can be sent.
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true));
    expect((screen.getByRole("button", { name: "Refuser" }) as HTMLButtonElement).disabled).toBe(true);
    release(json(403, { code: "reauthentication_required", title: "Recent authentication required" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(passkey.login).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/v1/approvals/ap1/approve", "/v1/approvals/ap1/approve"]);
  });

  it("explains an invalidated proposal and offers no decision", () => {
    wrap(<ApprovalReview approval={{ ...shared, status: "invalidated", error: "event_changed" }} />);
    expect(screen.getByText("Plus valable", { selector: "span" })).toBeTruthy();
    expect(screen.getByText(/a changé dans l'agenda depuis l'aperçu/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Approuver/ })).toBeNull();
  });

  it("blocks a proposed slot through an approval, with an idempotency key", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      json(201, { ...shared, action: "calendar.create_event", level: "approval", preview: { ...shared.preview, operation: "create", shared: false, attendees: [], before: undefined } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    wrap(<BlockSlotButton spaceId="perso" start="2026-10-29T09:00:00+01:00" end="2026-10-29T10:00:00+01:00" title="Rapport" />);
    fireEvent.click(screen.getByRole("button", { name: "Bloquer ce créneau" }));
    await screen.findByRole("button", { name: "Approuver" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/calendar/events");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      space_id: "perso",
      summary: "Rapport",
      starts_at: "2026-10-29T09:00:00+01:00",
      ends_at: "2026-10-29T10:00:00+01:00",
    });
  });

  it("proposes moving an event at local times, then shows the preview inline", async () => {
    const event: CalendarEvent = {
      id: "ev1",
      space_id: "perso",
      source_id: "src",
      source_name: "Agenda",
      synced_at: null,
      summary: "Dentiste",
      description: null,
      location: null,
      all_day: false,
      starts_at: "2026-10-28T14:00:00+01:00",
      ends_at: "2026-10-28T14:45:00+01:00",
      start_date: null,
      end_date: null,
      timezone: "Europe/Paris",
      status: "confirmed",
      busy: true,
      recurring: false,
      response: null,
      editable: true,
    };
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      json(201, { ...shared, action: "calendar.move_event", level: "approval" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    wrap(<EventActions event={event} />);
    fireEvent.click(screen.getByRole("button", { name: "Déplacer" }));
    fireEvent.change(screen.getByLabelText("Début"), { target: { value: "16:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "16:45" } });
    fireEvent.click(screen.getByRole("button", { name: "Proposer le déplacement" }));
    await screen.findByLabelText("Aperçu à approuver");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/calendar/events/ev1/move");
    expect(JSON.parse(String(init.body))).toEqual({
      starts_at: "2026-10-28T16:00",
      ends_at: "2026-10-28T16:45",
      notify: false,
    });
  });

  it("splits attendee addresses", () => {
    expect(parseGuests(" a@x.org, b@y.org;c@z.org ")).toEqual(["a@x.org", "b@y.org", "c@z.org"]);
  });
});
