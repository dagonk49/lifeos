import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceRecorder } from "./VoiceRecorder";

type Call = { url: string; method: string; body: BodyInit | null | undefined };

class FakeRecorder {
  static isTypeSupported = (type: string) => type.startsWith("audio/webm");
  static last: FakeRecorder | null = null;
  static created = 0;
  state: "inactive" | "recording" = "inactive";
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(_stream: unknown, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? "";
    FakeRecorder.last = this;
    FakeRecorder.created += 1;
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["opus"], { type: this.mimeType }) });
    this.onstop?.();
  }
}

const stopTrack = vi.fn();

function mockApi(dictation: Record<string, unknown>, maxSeconds = 900): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ url, method, body: init.body });
      if (url === "/v1/voice/status") return Response.json({ transcription: true, max_seconds: maxSeconds });
      if (method === "POST") {
        return Response.json({ ...dictation, status: "pending", text: null }, { status: 202 });
      }
      if (method === "DELETE") return new Response(null, { status: 204 });
      return Response.json(dictation);
    }),
  );
  return calls;
}

function renderRecorder(onTranscript: (text: string) => void, spaceId: string | null = "s1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <VoiceRecorder spaceId={spaceId} onTranscript={onTranscript} />
    </QueryClientProvider>,
  );
}

const base = {
  id: "d1",
  space_id: "s1",
  error: null,
  duration: 4.2,
  expires_at: "2026-10-13T10:00:00Z",
  created_at: "2026-10-12T10:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] })) },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  stopTrack.mockClear();
  FakeRecorder.created = 0;
});

describe("VoiceRecorder", () => {
  it("records, has the server transcribe, hands the text over and forgets the dictation", async () => {
    const calls = mockApi({ ...base, status: "ready", text: "Acheter du pain demain." });
    const onTranscript = vi.fn();
    renderRecorder(onTranscript);

    fireEvent.click(await screen.findByRole("button", { name: "Dicter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Terminer la dictée" }));

    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith("Acheter du pain demain."));
    expect(onTranscript).toHaveBeenCalledTimes(1);
    expect(stopTrack).toHaveBeenCalled(); // the microphone is released
    const sent = calls.find((c) => c.method === "POST");
    expect(sent?.url).toBe("/v1/voice/dictations");
    const form = sent?.body as FormData;
    expect(form.get("space_id")).toBe("s1");
    expect((form.get("file") as File).name).toBe("dictee.webm");
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url === "/v1/voice/dictations/d1")).toBe(true),
    );
    expect(await screen.findByText(/relisez-le et corrigez-le/)).toBeTruthy();
  });

  it("explains a failed transcription and sends nothing on cancel", async () => {
    const calls = mockApi({ ...base, status: "failed", error: "too_long", text: null });
    const onTranscript = vi.fn();
    renderRecorder(onTranscript);

    fireEvent.click(await screen.findByRole("button", { name: "Dicter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Annuler" }));
    await screen.findByRole("button", { name: "Dicter" });
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Dicter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Terminer la dictée" }));
    expect(await screen.findByText("L'enregistrement dépasse la durée maximale.")).toBeTruthy();
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("is hidden when dictation is disabled on the server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ transcription: false, max_seconds: 900 })),
    );
    renderRecorder(vi.fn());
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Dicter" })).toBeNull();
  });

  it("says when the microphone is refused", async () => {
    mockApi({ ...base, status: "ready", text: "x" });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () => {
          throw new DOMException("denied", "NotAllowedError");
        }),
      },
    });
    renderRecorder(vi.fn());
    fireEvent.click(await screen.findByRole("button", { name: "Dicter" }));
    expect(await screen.findByText(/Accès au micro refusé/)).toBeTruthy();
  });

  it("stops on its own before the server's limit, so the recording is never refused", async () => {
    const calls = mockApi({ ...base, status: "ready", text: "Une dictée longue." }, 3);
    renderRecorder(vi.fn());
    fireEvent.click(await screen.findByRole("button", { name: "Dicter" }));
    await screen.findByRole("button", { name: "Terminer la dictée" });
    // Limit 3 s, margin 2 s: stopped after 1 s without any click, then sent.
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true), { timeout: 3000 });
    expect(stopTrack).toHaveBeenCalled();
  });

  it("opens the microphone once, even when clicked twice, and releases it if the page is left", async () => {
    mockApi({ ...base, status: "ready", text: "x" });
    let grant: (stream: unknown) => void = () => {};
    const getUserMedia = vi.fn(() => new Promise((resolve) => (grant = resolve)));
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    renderRecorder(vi.fn());
    const button = await screen.findByRole("button", { name: "Dicter" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Accès au micro…" })).toHaveProperty("disabled", true);
    // The page is left while the permission prompt is open: the late stream is released.
    cleanup();
    grant({ getTracks: () => [{ stop: stopTrack }] });
    await waitFor(() => expect(stopTrack).toHaveBeenCalled());
    expect(FakeRecorder.created).toBe(0);
  });
});
