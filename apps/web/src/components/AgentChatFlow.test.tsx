import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "@/app/(app)/chat/page";
import { SpaceProvider } from "@/lib/space";
import type { AgentInfo, ConversationDetail, Message, SendResult } from "@/lib/types";

vi.mock("@/lib/passkeys", () => ({ loginWithPasskey: vi.fn(async () => ({})) }));

function msg(overrides: Partial<Message>): Message {
  return {
    id: "m1",
    conversation_id: "c1",
    role: "user",
    content: "Bonjour",
    status: "ok",
    model: null,
    agent: null,
    purge_after: "2027-01-09T08:00:00Z",
    retained: false,
    created_at: "2026-10-11T08:00:00Z",
    ...overrides,
  };
}

const detail: ConversationDetail = {
  id: "c1",
  space_id: "s1",
  title: "Ma semaine",
  last_message_at: "2026-10-11T08:00:05Z",
  consolidated_until: null,
  created_at: "2026-10-11T08:00:00Z",
  messages: [
    msg({ id: "m1", content: "Organise ma semaine" }),
    msg({ id: "m2", role: "assistant", content: "Voici un plan.", agent: "planning", created_at: "2026-10-11T08:00:05Z" }),
  ],
};

function agents(missing: string[]): AgentInfo[] {
  return [
    {
      name: "planning",
      label: "Planification",
      description: "Priorise les tâches, répartit la charge.",
      resources: ["tasks", "goals"],
      missing_permissions: missing,
    },
    { name: "memory", label: "Mémoire", description: "Retrouve ce que LifeOS sait.", resources: [], missing_permissions: [] },
  ];
}

function sent(overrides: Partial<SendResult>): SendResult {
  return {
    user_message: msg({ id: "u-new", content: "Que faire en premier ?" }),
    assistant_message: null,
    error: null,
    context_items: 0,
    agent: "core",
    suggested_agent: null,
    ...overrides,
  };
}

type Call = { url: string; method: string; body: unknown };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi(replies: SendResult[]): Call[] {
  const calls: Call[] = [];
  let granted = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/v1/chat/conversations" && method === "GET") return json({ items: [detail], next_cursor: null });
      if (url === "/v1/chat/conversations/c1") return json(detail);
      if (url.startsWith("/v1/agents?")) return json(agents(granted ? [] : ["tasks", "goals"]));
      if (url === "/v1/agents/planning/access") {
        granted = true;
        return json(agents([]));
      }
      if (url === "/v1/chat/conversations/c1/messages") return json(replies.shift(), 201);
      return json({ title: "Not found", status: 404, code: "not_found" }, 404);
    }),
  );
  return calls;
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Personnel" }]}>
        <ChatPage />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

async function openConversation() {
  fireEvent.click(await screen.findByRole("button", { name: /Ma semaine/ }));
  await screen.findByText("Voici un plan.");
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Assistant page: specialised agents", () => {
  it("labels agent replies and sends the chosen agent, then grants access and resends", async () => {
    const calls = mockApi([
      sent({
        agent: "planning",
        error: {
          code: "agent_permission_required",
          title: "This agent has no access to this space",
          detail: null,
          missing: ["tasks", "goals"],
        },
      }),
      sent({ agent: "planning", assistant_message: msg({ id: "a-new", role: "assistant", agent: "planning" }) }),
    ]);
    renderPage();
    await openConversation();

    const list = screen.getByRole("list", { name: "Messages" });
    expect(within(list).getByText("Planification")).toBeTruthy();

    const select = screen.getByLabelText("Agent") as HTMLSelectElement;
    expect(select.value).toBe("core");
    await screen.findByRole("option", { name: "Planification" });
    fireEvent.change(select, { target: { value: "planning" } });
    expect(await screen.findByText(/Accès en lecture à autoriser/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Que faire en premier ?" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    fireEvent.click(await screen.findByRole("button", { name: "Autoriser" }));
    fireEvent.click(await screen.findByRole("button", { name: "Renvoyer" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Renvoyer" })).toBeNull());
    const sends = calls.filter((c) => c.url === "/v1/chat/conversations/c1/messages");
    expect(sends.map((c) => c.body)).toEqual([
      { content: "Que faire en premier ?", context: { screen: "chat" }, agent: "planning", attachments: [] },
      { content: "Que faire en premier ?", context: { screen: "chat" }, agent: "planning", attachments: [] },
    ]);
    const grant = calls.find((c) => c.url === "/v1/agents/planning/access");
    expect(grant).toMatchObject({ method: "PUT", body: { space_id: "s1", enabled: true } });
  });

  it("offers the suggested agent for the next message, never automatically", async () => {
    const calls = mockApi([sent({ suggested_agent: "planning" })]);
    renderPage();
    await openConversation();

    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Planifier ma semaine" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    const chip = await screen.findByRole("button", {
      name: "L'agent Planification pourrait vous aider : l'utiliser pour le prochain message",
    });
    const send = calls.find((c) => c.url === "/v1/chat/conversations/c1/messages");
    expect(send?.body).toMatchObject({ agent: "core" });
    expect((screen.getByLabelText("Agent") as HTMLSelectElement).value).toBe("core");

    fireEvent.click(chip);
    expect((screen.getByLabelText("Agent") as HTMLSelectElement).value).toBe("planning");
    expect(screen.queryByRole("button", { name: /pourrait vous aider/ })).toBeNull();
  });

  it("lets the owner dismiss the suggestion", async () => {
    mockApi([sent({ suggested_agent: "memory" })]);
    renderPage();
    await openConversation();
    fireEvent.change(screen.getByLabelText("Votre message"), { target: { value: "Tu te souviens ?" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    fireEvent.click(await screen.findByRole("button", { name: "Ignorer la suggestion" }));
    expect(screen.queryByRole("button", { name: /pourrait vous aider/ })).toBeNull();
    expect((screen.getByLabelText("Agent") as HTMLSelectElement).value).toBe("core");
  });
});
