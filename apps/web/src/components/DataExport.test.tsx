import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DataExport } from "./DataExport";

const loginWithPasskey = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/passkeys", () => ({ loginWithPasskey }));

const REAUTH = {
  title: "Forbidden",
  status: 403,
  code: "reauthentication_required",
};

function problem(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 403,
    headers: { "Content-Type": "application/problem+json" },
  });
}

function zip(): Response {
  return new Response("PK", {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition":
        'attachment; filename="lifeos-export-20261027.zip"',
    },
  });
}

function renderPanel() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <DataExport />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "URL",
    Object.assign(URL, {
      createObjectURL: vi.fn(() => "blob:x"),
      revokeObjectURL: vi.fn(),
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  loginWithPasskey.mockReset();
});

describe("DataExport", () => {
  it("downloads the archive or the JSON through fetch, with a warning", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(zip())
      .mockResolvedValueOnce(zip());
    vi.stubGlobal("fetch", fetchMock);
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    renderPanel();
    expect(screen.getByText(/ne sont pas chiffrés/)).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Télécharger l'archive (ZIP)" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/Fichier téléchargé/)).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "JSON seul" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/v1/privacy/export.zip",
      "/v1/privacy/export",
    ]);
    expect(loginWithPasskey).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("asks for the passkey when the sign-in is not recent, then downloads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(problem(REAUTH))
      .mockResolvedValueOnce(zip());
    vi.stubGlobal("fetch", fetchMock);
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    renderPanel();
    fireEvent.click(screen.getByLabelText(/Inclure les originaux déchiffrés/));
    fireEvent.click(
      screen.getByRole("button", { name: "Télécharger l'archive (ZIP)" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/Fichier téléchargé/)).toBeTruthy(),
    );
    expect(loginWithPasskey).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/v1/privacy/export.zip?originals=true",
      "/v1/privacy/export.zip?originals=true",
    ]);
    expect(click).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/lifeos-export-20261027\.zip/)).toBeTruthy();
    click.mockRestore();
  });

  it("explains a refusal without downloading anything", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(problem(REAUTH)));
    loginWithPasskey.mockRejectedValueOnce(
      new DOMException("cancelled", "NotAllowedError"),
    );
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "JSON seul" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("annulée");
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
