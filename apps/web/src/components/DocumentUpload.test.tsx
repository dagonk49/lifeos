import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpaceProvider } from "@/lib/space";
import type { DocumentDetail, Space } from "@/lib/types";

import { DocumentUpload } from "./DocumentUpload";

const created: DocumentDetail = {
  id: "d9",
  space_id: "s1",
  title: "Ticket Lidl",
  kind: "pdf",
  mime: "application/pdf",
  size_bytes: 1_258_291,
  purpose: "receipt",
  status: "pending",
  error: null,
  sensitivity: [],
  text_source: null,
  text_confidence: null,
  pages: null,
  processed_at: null,
  text_corrected_at: null,
  original_available: true,
  original_until: "2027-01-10T10:00:00Z",
  keep_original: false,
  local_only: false,
  source_type: null,
  source_id: null,
  analysis: null,
  version: 1,
  created_at: "2026-10-12T10:00:00Z",
  updated_at: "2026-10-12T10:00:00Z",
  extracted_text: null,
};

type Call = { url: string; method: string; headers: Record<string, string>; form: FormData };

function mockUpload(status = 201, body: unknown = created): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({
        url,
        method: init.method ?? "GET",
        headers: init.headers as Record<string, string>,
        form: init.body as FormData,
      });
      return new Response(JSON.stringify(body), { status });
    }),
  );
  return calls;
}

const PERSO: Space = { id: "s1", kind: "personal", name: "Perso" };
const PRO: Space = { id: "s2", kind: "professional", name: "Travail" };

function renderUpload(spaces: Space[] = [PERSO], onUploaded = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SpaceProvider spaces={spaces}>
        <DocumentUpload onUploaded={onUploaded} />
      </SpaceProvider>
    </QueryClientProvider>,
  );
  return onUploaded;
}

function chooseFile(name = "ticket.pdf", size = 1_258_291) {
  const file = new File([new Uint8Array(size)], name, { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Fichier"), { target: { files: [file] } });
  return file;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DocumentUpload", () => {
  it("sends the file as a receipt with an idempotency key, then opens it", async () => {
    const calls = mockUpload();
    const onUploaded = renderUpload();
    expect((screen.getByRole("button", { name: "Envoyer" }) as HTMLButtonElement).disabled).toBe(true);

    chooseFile();
    // Name and French size of the chosen file.
    expect(screen.getByText("ticket.pdf")).toBeTruthy();
    expect(screen.getByText(/\(1,2 Mo\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Ticket de caisse" }));
    fireEvent.change(screen.getByLabelText("Titre (facultatif)"), { target: { value: "Ticket Lidl" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Conserver l'original sans limite" }));
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(created));
    const call = calls[0];
    expect(call?.url).toBe("/v1/documents");
    expect(call?.method).toBe("POST");
    expect(call?.headers["Idempotency-Key"]).toBeTruthy();
    expect(call?.form.get("space_id")).toBe("s1");
    expect(call?.form.get("purpose")).toBe("receipt");
    expect(call?.form.get("title")).toBe("Ticket Lidl");
    expect(call?.form.get("keep_original")).toBe("true");
    expect((call?.form.get("file") as File).name).toBe("ticket.pdf");

    // The retention date comes from the server's answer.
    expect(await screen.findByText(/« Ticket Lidl » est envoyé : lecture en cours\. L'original chiffré sera supprimé le/)).toBeTruthy();
  });

  it("asks for the space when all spaces are shown", async () => {
    const calls = mockUpload();
    renderUpload([PERSO, PRO]);
    chooseFile();
    fireEvent.change(screen.getByLabelText("Espace"), { target: { value: "s2" } });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]?.form.get("space_id")).toBe("s2");
    expect(calls[0]?.form.get("purpose")).toBe("document");
    expect(calls[0]?.form.has("title")).toBe(false);
    expect(calls[0]?.form.get("keep_original")).toBe("false");
  });

  it("refuses an empty file without calling the server", () => {
    const calls = mockUpload();
    renderUpload();
    chooseFile("vide.txt", 0);
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(screen.getByRole("alert").textContent).toBe("Le fichier est vide.");
    expect(calls).toHaveLength(0);
  });

  it("shows the server's size limit when the file is too large", async () => {
    mockUpload(413, { title: "Too large", status: 413, code: "document_too_large", max_bytes: 20 * 1024 * 1024 });
    const onUploaded = renderUpload();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));
    expect(await screen.findByText("Le fichier est trop volumineux (20 Mo au maximum).")).toBeTruthy();
    expect(onUploaded).not.toHaveBeenCalled();
  });
});
