import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, attachmentName, download, errorMessage, withReauth } from "./api";

function respond(status: number, body: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("sends JSON and an idempotency key for creations", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: "1" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await api<{ id: string }>("/v1/tasks", {
      method: "POST",
      body: { title: "x" },
      idempotent: true,
    });
    expect(result.id).toBe("1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(init.body).toBe('{"title":"x"}');
    expect(init.credentials).toBe("same-origin");
  });

  it("turns problem documents into ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(403, { status: 403, code: "cloud_consent_required", title: "No" })),
    );
    const error = await api("/v1/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("cloud_consent_required");
    expect(errorMessage(error)).toContain("pas activé pour cet espace");
  });

  it("explains a write refused for its origin (CSRF defence) without blaming the owner", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(403, { status: 403, code: "origin_mismatch", title: "Origin not allowed" })),
    );
    const error = await api("/v1/tasks", { method: "POST", body: { title: "x" } }).catch((e: unknown) => e);
    expect(errorMessage(error)).toContain("Rechargez la page");
  });

  it("returns undefined for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api("/v1/x", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("withReauth", () => {
  it("re-authenticates once then retries", async () => {
    const operation = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(403, { status: 403, code: "reauthentication_required", title: "" }))
      .mockResolvedValueOnce("ok");
    const reauth = vi.fn().mockResolvedValue(undefined);
    await expect(withReauth(operation, reauth)).resolves.toBe("ok");
    expect(reauth).toHaveBeenCalledOnce();
    expect(operation).toHaveBeenCalledTimes(2);
  });

  it("does not re-authenticate for other errors", async () => {
    const reauth = vi.fn();
    const failure = new ApiError(404, { status: 404, code: "task_not_found", title: "" });
    await expect(withReauth(() => Promise.reject(failure), reauth)).rejects.toBe(failure);
    expect(reauth).not.toHaveBeenCalled();
  });
});

describe("download", () => {
  it("reads the attachment name, RFC 8187 form first", () => {
    expect(attachmentName('attachment; filename="a.zip"', "x")).toBe("a.zip");
    expect(attachmentName("attachment; filename=\"Releve.pdf\"; filename*=UTF-8''Relev%C3%A9.pdf", "x")).toBe(
      "Relevé.pdf",
    );
    expect(attachmentName(null, "x.zip")).toBe("x.zip");
  });

  it("saves the file and throws problems as ApiError", async () => {
    const createObjectURL = vi.fn(() => "blob:1");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response("PK", { headers: { "Content-Disposition": 'attachment; filename="e.zip"' } }),
        )
        .mockResolvedValueOnce(respond(403, { status: 403, code: "reauthentication_required", title: "" })),
    );
    await expect(download("/v1/privacy/export.zip", "x.zip")).resolves.toBe("e.zip");
    expect(click).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();
    const error = await download("/v1/privacy/export.zip", "x.zip").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("reauthentication_required");
    expect(click).toHaveBeenCalledOnce();
    click.mockRestore();
  });
});
