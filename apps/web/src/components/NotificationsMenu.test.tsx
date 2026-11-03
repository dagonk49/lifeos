import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NotificationItem } from "@/lib/types";

import { NotificationsMenu } from "./NotificationsMenu";

function item(overrides: Partial<NotificationItem>): NotificationItem {
  return {
    id: "n1",
    space_id: null,
    category: "important",
    title: "Rappel : Arroser",
    body: null,
    status: "pending",
    source_type: "task",
    source_id: "t1",
    deliver_after: "2026-10-25T16:00:00Z",
    created_at: "2026-10-25T16:00:00Z",
    read_at: null,
    items_count: 0,
    ...overrides,
  };
}

const digest = item({
  id: "d1",
  category: "suggestion",
  title: "Suggestions regroupées",
  body: "2 suggestions",
  source_type: "digest",
  source_id: null,
  items_count: 2,
});
const children = [
  item({ id: "s1", category: "suggestion", title: "Votre bilan quotidien est prêt (Personnel)", source_type: "review" }),
  item({ id: "s2", category: "suggestion", title: "Votre bilan quotidien est prêt (Travail)", source_type: "review" }),
];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("NotificationsMenu", () => {
  it("expands a digest and dismisses a notification", async () => {
    const calls: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (url === "/v1/notifications/d1/items") return json(200, children);
        if (method === "PATCH") return json(200, { ...digest, status: "dismissed" });
        return json(200, [digest, item({})]);
      }),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <NotificationsMenu />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    expect(await screen.findByText("2 suggestions")).toBeTruthy();
    // Grouped suggestions are fetched only when the digest is opened.
    expect(calls.some((c) => c.url.endsWith("/items"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Voir le détail" }));
    expect(await screen.findByText("Votre bilan quotidien est prêt (Travail)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Masquer le détail" }).getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Ignorer « Suggestions regroupées »" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")).toEqual({
      url: "/v1/notifications/d1",
      method: "PATCH",
      body: { status: "dismissed" },
    });
  });
});

function renderMenu(calls: { url: string; method: string }[], list: NotificationItem[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "POST") return new Response(null, { status: 204 });
      return json(200, list);
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <button type="button">Ailleurs</button>
      <NotificationsMenu />
    </QueryClientProvider>,
  );
}

describe("NotificationsMenu disclosure", () => {
  it("is a disclosure: Escape closes the panel and gives the focus back", async () => {
    renderMenu([], [item({})]);
    await screen.findByText("1");
    const trigger = screen.getByRole("button", { name: "Notifications, 1 en attente" });
    expect(trigger.getAttribute("aria-haspopup")).toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    const panel = document.getElementById(trigger.getAttribute("aria-controls") ?? "");
    expect(panel?.hidden).toBe(true);

    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const read = await screen.findByRole("button", { name: "Marquer « Rappel : Arroser » comme lu" });
    read.focus();
    fireEvent.keyDown(read, { key: "Escape" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(panel?.hidden).toBe(true);
    expect(document.activeElement).toBe(trigger);
  });

  it("closes on a click outside", async () => {
    renderMenu([], [item({})]);
    fireEvent.click(await screen.findByRole("button", { name: /^Notifications/ }));
    expect(await screen.findByRole("group", { name: "Notifications" })).toBeTruthy();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Ailleurs" }));
    expect(screen.getByRole("button", { name: /^Notifications/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("names each action after its notification and marks everything as read", async () => {
    const calls: { url: string; method: string }[] = [];
    renderMenu(calls, [item({}), item({ id: "n2", title: "Rappel : Appeler" })]);
    fireEvent.click(await screen.findByRole("button", { name: /^Notifications/ }));
    expect(await screen.findByRole("button", { name: "Marquer « Rappel : Appeler » comme lu" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ignorer « Rappel : Appeler »" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Tout marquer comme lu" }));
    await waitFor(() => expect(calls).toContainEqual({ url: "/v1/notifications/read-all", method: "POST" }));
  });
});
