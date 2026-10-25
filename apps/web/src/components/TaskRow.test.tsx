import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { Task } from "@/lib/types";

import { TaskRow } from "./TaskRow";

const BLOCKED: Task = {
  id: "t2",
  space_id: "s1",
  title: "Signer le devis",
  notes: null,
  status: "todo",
  priority: "normal",
  due_at: null,
  remind_at: null,
  completed_at: null,
  source_type: "manual",
  source_id: null,
  goal_id: null,
  tags: ["maison"],
  depends_on: ["t1"],
  blocked_by: [{ id: "t1", title: "Demander le devis", status: "todo" }],
  blocked: true,
  version: 2,
  created_at: "2026-10-01T08:00:00Z",
  updated_at: "2026-10-01T08:00:00Z",
};

const FREE: Task = {
  ...BLOCKED,
  id: "t3",
  title: "Payer l'acompte",
  tags: [],
  depends_on: [],
  blocked_by: [],
  blocked: false,
};

type Call = { url: string; method: string; body: unknown };

function mockApi(respond: (call: Call) => [number, unknown]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const call = {
        url,
        method: init.method ?? "GET",
        body: init.body ? JSON.parse(init.body as string) : undefined,
      };
      calls.push(call);
      const [status, data] = respond(call);
      return new Response(status === 204 ? null : JSON.stringify(data), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

function renderRow(task: Task, candidates?: Task[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={[{ id: "s1", kind: "personal", name: "Perso" }]}>
        <ul>
          <TaskRow task={task} candidates={candidates} />
        </ul>
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TaskRow", () => {
  it("explains why a blocked task cannot be finished", () => {
    renderRow(BLOCKED);
    const checkbox = screen.getByRole("checkbox", { name: "Terminer « Signer le devis »" });
    expect((checkbox as HTMLInputElement).disabled).toBe(true);
    const hint = document.getElementById(checkbox.getAttribute("aria-describedby") ?? "");
    expect(hint?.textContent).toBe("Bloquée par « Demander le devis » : terminez ou annulez d'abord cette tâche.");
    expect(screen.getByText("Bloquée")).toBeTruthy();
    expect(screen.getByText("maison")).toBeTruthy();
  });

  it("lets a free task be finished", async () => {
    const calls = mockApi(() => [200, { ...FREE, status: "done" }]);
    renderRow(FREE);
    const checkbox = screen.getByRole("checkbox", { name: "Terminer « Payer l'acompte »" });
    expect((checkbox as HTMLInputElement).disabled).toBe(false);
    expect(checkbox.getAttribute("aria-describedby")).toBeNull();
    fireEvent.click(checkbox);
    await waitFor(() => expect(calls[0]).toMatchObject({ method: "PATCH", body: { status: "done" } }));
  });

  it("links a dependency and explains a refused cycle", async () => {
    const calls = mockApi(() => [
      422,
      { title: "Cycle", status: 422, code: "dependency_cycle", path: ["t3", "t2", "t1", "t3"] },
    ]);
    renderRow(FREE, [BLOCKED]);
    fireEvent.click(screen.getByRole("button", { name: "Dépendances" }));
    expect(screen.getByText("N'attend aucune tâche ouverte.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Attendre aussi"), { target: { value: "t2" } });
    fireEvent.click(screen.getByRole("button", { name: "Lier" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/créerait une boucle/);
    expect(calls[0]).toMatchObject({
      url: "/v1/tasks/t3/dependencies",
      method: "POST",
      body: { depends_on_id: "t2" },
    });
  });

  it("stops waiting for a task", async () => {
    const calls = mockApi(() => [204, null]);
    renderRow(BLOCKED, [FREE]);
    fireEvent.click(screen.getByRole("button", { name: "Dépendances (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Ne plus attendre « Demander le devis »" }));
    await waitFor(() =>
      expect(calls[0]).toMatchObject({ url: "/v1/tasks/t2/dependencies/t1", method: "DELETE" }),
    );
  });
});
