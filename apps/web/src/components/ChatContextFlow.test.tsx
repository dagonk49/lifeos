import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "@/app/(app)/chat/page";
import { SpaceProvider } from "@/lib/space";
import type { AgentInfo, ConversationDetail, Message, SendResult } from "@/lib/types";

// « Discuter de cet objectif » opens /chat?about=goal:<id>: the address is mocked here.
const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => "/chat",
}));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

const GOAL = "0b9c8f6e-1d2a-4c3b-9e8f-7a6b5c4d3e2f";
const TASK = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f";

function msg(overrides: Partial<Message>): Message {
  return {
    id: "m1",
    conversation_id: "c-new",
    role: "user",
    content: "Comment avancer ?",
    status: "ok",
    model: null,
    agent: null,
    purge_after: "2027-01-09T08:00:00Z",
    retained: false,
    created_at: "2026-10-11T08:00:00Z",
    ...overrides,
  };
}

const existing: ConversationDetail = {
  id: "c1",
  space_id: "s1",
  title: "Ma semaine",
  last_message_at: "2026-10-11T08:00:05Z",
  consolidated_until: null,
  created_at: "2026-10-11T08:00:00Z",
  messages: [msg({ id: "old", conversation_id: "c1", content: "Bonjour" })],
};

const coaching: AgentInfo = {
  name: "coaching",
  label: "Coaching",
  description: "Fait le point sur les objectifs.",
  resources: ["goals", "journal", "reviews"],
  missing_permissions: [],
};

type Call = { url: string; method: string; body: unknown };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi({ objectSpace = "s1", conversations = [] as ConversationDetail[] } = {}): Call[] {
  const calls: Call[] = [];
  const created: ConversationDetail[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (url.startsWith("/v1/chat/conversations") && method === "GET" && !url.includes("/conversations/"))
        return json({ items: [...conversations, ...created], next_cursor: null });
      if (url === "/v1/chat/conversations" && method === "POST") {
        const conversation: ConversationDetail = {
          ...existing,
          id: `c-new-${created.length + 1}`,
          space_id: body.space_id,
          title: "Nouvelle conversation",
          messages: [],
        };
        created.push(conversation);
        return json(conversation, 201);
      }
      const opened = [...conversations, ...created].find((c) => url === `/v1/chat/conversations/${c.id}`);
      if (opened) return json(opened);
      const sending = [...conversations, ...created].find((c) => url === `/v1/chat/conversations/${c.id}/messages`);
      if (sending) {
        const user = msg({ id: `u${calls.length}`, conversation_id: sending.id, context: body.context.ref ?? null });
        sending.messages = [...sending.messages, user];
        const result: SendResult = {
          user_message: user,
          assistant_message: null,
          error: null,
          context_items: 0,
          agent: body.agent,
          suggested_agent: null,
        };
        return json(result, 201);
      }
      if (url === `/v1/goals/${GOAL}`) return json({ id: GOAL, space_id: objectSpace, title: "Courir 10 km" });
      if (url === `/v1/tasks/${TASK}`) return json({ id: TASK, space_id: objectSpace, title: "Appeler le club" });
      if (url.startsWith("/v1/agents?")) return json([coaching]);
      if (url === "/v1/chat/preview") return json({ masked: {}, attachments: [] });
      return json({ title: "Not found", status: 404, code: "not_found" }, 404);
    }),
  );
  return calls;
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SpaceProvider
        spaces={[
          { id: "s1", kind: "personal", name: "Personnel" },
          { id: "s2", kind: "professional", name: "Pro" },
        ]}
      >
        <ChatPage />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

async function sendMessage(text: string) {
  fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: text } });
  const button = screen.getByRole("button", { name: "Envoyer" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
}

const sends = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.url.endsWith("/messages"));

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  nav.replace.mockReset();
  nav.replace.mockImplementation((href: string) => {
    nav.search = new URL(href, "https://lifeos.test").search;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  nav.search = "";
});

describe("Assistant page: « Discuter de… »", () => {
  it("shows the chosen goal, sends it as a reference, then stops once removed", async () => {
    nav.search = `?about=goal:${GOAL}`;
    const calls = mockApi();
    renderPage();

    const chip = await screen.findByRole("group", { name: "Élément joint au message" });
    expect(await within(chip).findByText("À propos de « Courir 10 km » (Objectif)")).toBeTruthy();

    await sendMessage("Comment avancer ?");
    await waitFor(() => expect(sends(calls)).toHaveLength(1));
    expect(calls.find((c) => c.url === "/v1/chat/conversations" && c.method === "POST")?.body).toEqual({
      space_id: "s1",
    });
    expect(sends(calls)[0]?.body).toMatchObject({
      content: "Comment avancer ?",
      context: { screen: "chat", ref: { type: "goal", id: GOAL } },
    });
    // The message says what it was about.
    const linked = await screen.findByRole("link", { name: "Objectif lié" });
    expect(linked.getAttribute("href")).toBe(`/goals?id=${GOAL}`);

    fireEvent.click(within(chip).getByRole("button", { name: "Retirer le contexte" }));
    expect(nav.replace).toHaveBeenCalledWith("/chat");
    await waitFor(() => expect(screen.queryByRole("group", { name: "Élément joint au message" })).toBeNull());

    await sendMessage("Et ensuite ?");
    await waitFor(() => expect(sends(calls)).toHaveLength(2));
    expect(sends(calls)[1]?.body).toMatchObject({ content: "Et ensuite ?", context: { screen: "chat" } });
    expect((sends(calls)[1]?.body as { context: object } | undefined)?.context).not.toHaveProperty("ref");
  });

  it("opens the conversation in the object's space, not the default one", async () => {
    nav.search = `?about=goal:${GOAL}`;
    const calls = mockApi({ objectSpace: "s2" });
    renderPage();
    await screen.findByText("À propos de « Courir 10 km » (Objectif)");

    await sendMessage("Où en suis-je ?");
    await waitFor(() => expect(sends(calls)).toHaveLength(1));
    const created = calls.filter((c) => c.url === "/v1/chat/conversations" && c.method === "POST");
    expect(created.map((c) => c.body)).toEqual([{ space_id: "s2" }]);
    // The agents and the control before sending concern that space too.
    expect(calls.some((c) => c.url === "/v1/agents?space_id=s2")).toBe(true);
    expect(calls.find((c) => c.url === "/v1/chat/preview")?.body).toMatchObject({ space_id: "s2" });
  });

  it("never sends the object into a conversation of another space", async () => {
    nav.search = `?about=goal:${GOAL}`;
    const calls = mockApi({ objectSpace: "s2", conversations: [structuredClone(existing)] });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Ma semaine/ }));
    await screen.findByText("Bonjour");
    expect(
      await screen.findByText(/Une nouvelle conversation sera ouverte dans l'espace « Pro »/),
    ).toBeTruthy();

    await sendMessage("On en parle ?");
    await waitFor(() => expect(sends(calls)).toHaveLength(1));
    expect(sends(calls)[0]?.url).toBe("/v1/chat/conversations/c-new-1/messages");
    expect(calls.find((c) => c.url === "/v1/chat/conversations" && c.method === "POST")?.body).toEqual({
      space_id: "s2",
    });
  });

  it("warns before sending a task to an agent that does not read tasks", async () => {
    nav.search = `?about=task:${TASK}`;
    const calls = mockApi();
    renderPage();
    await screen.findByText("À propos de « Appeler le club » (Tâche)");

    const select = screen.getByLabelText("Agent") as HTMLSelectElement;
    await screen.findByRole("option", { name: "Coaching" });
    fireEvent.change(select, { target: { value: "coaching" } });
    expect(await screen.findByText(/L'agent Coaching ne lit pas les tâches/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Aide-moi" } });
    expect((screen.getByRole("button", { name: "Envoyer" }) as HTMLButtonElement).disabled).toBe(true);
    expect(sends(calls)).toHaveLength(0);

    fireEvent.change(select, { target: { value: "core" } });
    await sendMessage("Aide-moi");
    await waitFor(() => expect(sends(calls)).toHaveLength(1));
    expect(sends(calls)[0]?.body).toMatchObject({ agent: "core", context: { ref: { type: "task", id: TASK } } });
  });

  it("ignores an address that does not name a task or a goal", async () => {
    nav.search = "?about=journal:abc";
    const calls = mockApi();
    renderPage();
    await screen.findByLabelText("Votre message");
    expect(screen.queryByRole("group", { name: "Élément joint au message" })).toBeNull();
    await sendMessage("Bonjour");
    await waitFor(() => expect(sends(calls)).toHaveLength(1));
    expect(sends(calls)[0]?.body).toMatchObject({ context: { screen: "chat" } });
  });
});
