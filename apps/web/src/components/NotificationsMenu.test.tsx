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
