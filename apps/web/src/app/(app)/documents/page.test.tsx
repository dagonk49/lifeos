import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { Space } from "@/lib/types";

import DocumentsPage from "./page";

const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  replace: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  usePathname: () => "/documents",
  useSearchParams: () => navigation.params,
}));

const PERSO: Space = { id: "s1", kind: "personal", name: "Perso" };

function document(id: string, title: string) {
  return { id, title, space_id: "s1", purpose: "document", status: "pending", original_until: null };
}

/** The service worker holding `files` (manifest share target). */
function stubWorker(files: File[]) {
  const target = new EventTarget();
  const postMessage = vi.fn(() =>
    queueMicrotask(() => target.dispatchEvent(new MessageEvent("message", { data: { type: "lifeos-share", files } }))),
  );
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: Object.assign(target, { controller: null, ready: Promise.resolve({ active: { postMessage } }) }),
  });
  return postMessage;
}

function mockApi() {
  const uploads: FormData[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      if (init.method === "POST") {
        const form = init.body as FormData;
        uploads.push(form);
        const file = form.get("file") as File;
        return new Response(JSON.stringify(document(`d${uploads.length}`, file.name)), { status: 201 });
      }
      return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
    }),
  );
  return uploads;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={[PERSO]}>
        <DocumentsPage />
      </SpaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "serviceWorker");
  navigation.params = new URLSearchParams();
  navigation.replace.mockClear();
  navigation.push.mockClear();
});

describe("documents page, files shared from the phone", () => {
  it("takes the shared files from the worker once, and sends them one by one on the owner's click", async () => {
    navigation.params = new URLSearchParams("partage=1");
    const files = [
      new File(["photo"], "ticket.jpg", { type: "image/jpeg" }),
      new File(["%PDF"], "facture.pdf", { type: "application/pdf" }),
    ];
    const postMessage = stubWorker(files);
    const uploads = mockApi();
    renderPage();

    expect(
      await screen.findByText(/^Fichier partagé 1 sur 2 : choisissez son type \(document ou ticket de caisse\)/),
    ).toBeTruthy();
    expect(postMessage).toHaveBeenCalledOnce();
    // The query parameter is dropped: a reload does not ask again.
    expect(navigation.replace).toHaveBeenCalledWith("/documents", { scroll: false });
    expect(screen.getByText("ticket.jpg")).toBeTruthy();
    // Nothing is sent before the owner chooses.
    expect(uploads).toHaveLength(0);

    fireEvent.click(screen.getByRole("radio", { name: "Ticket de caisse" }));
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(await screen.findByText(/« ticket\.jpg » est envoyé : lecture en cours\. Fichier partagé 2 sur 2/)).toBeTruthy();
    expect(uploads[0]?.get("purpose")).toBe("receipt");
    expect((uploads[0]?.get("file") as File).name).toBe("ticket.jpg");
    expect(screen.getByText("facture.pdf")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await waitFor(() => expect(navigation.push).toHaveBeenCalledWith("/documents?id=d2"));
    expect(uploads[1]?.get("purpose")).toBe("document");
    expect(screen.queryByText(/Fichier partagé/)).toBeNull();
  });

  it("asks to pick the file again when the worker has nothing", async () => {
    navigation.params = new URLSearchParams("partage=1");
    stubWorker([]);
    mockApi();
    renderPage();
    expect(await screen.findByText(/Le fichier partagé n'a pas pu être repris\./)).toBeTruthy();
  });

  it("asks to pick the file again when no worker took the share", async () => {
    navigation.params = new URLSearchParams("partage=indisponible");
    mockApi();
    renderPage();
    expect(screen.getByText(/Le fichier partagé n'a pas pu être repris\./)).toBeTruthy();
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/documents", { scroll: false }));
  });
});
