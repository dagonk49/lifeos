import { describe, expect, it, vi } from "vitest";

import manifest from "@/app/manifest";
import { GET, POST } from "@/app/partage/route";

import { ACCEPTED_FILES } from "./documents";
import { receiveSharedFiles, SHARE_ACCEPT, SHARE_PAGE_FAILED, sharedFileNotice } from "./share";

/** A fake ServiceWorkerContainer whose active worker answers with `reply` (or never). */
function container(reply: unknown | undefined, { active = true } = {}) {
  const target = new EventTarget();
  const postMessage = vi.fn((message: unknown) => {
    if (reply !== undefined) queueMicrotask(() => target.dispatchEvent(new MessageEvent("message", { data: reply })));
    return message;
  });
  const fake = Object.assign(target, {
    controller: null,
    startMessages: vi.fn(),
    ready: Promise.resolve({ active: active ? { postMessage } : null }),
  });
  return { fake: fake as unknown as ServiceWorkerContainer, postMessage };
}

describe("shared files", () => {
  it("asks the worker once and returns its files", async () => {
    const file = new File(["x"], "ticket.jpg", { type: "image/jpeg" });
    const { fake, postMessage } = container({ type: "lifeos-share", files: [file, "not a file"] });
    await expect(receiveSharedFiles(fake, 1000)).resolves.toEqual([file]);
    expect(postMessage).toHaveBeenCalledExactlyOnceWith({ type: "lifeos-share-request" });
  });

  it("ignores other messages and gives up when the worker does not answer", async () => {
    vi.useFakeTimers();
    try {
      const { fake } = container({ type: "something-else" });
      const files = receiveSharedFiles(fake, 1000);
      await vi.advanceTimersByTimeAsync(1000);
      await expect(files).resolves.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns null without a worker", async () => {
    await expect(receiveSharedFiles(undefined)).resolves.toBeNull();
    const { fake } = container(undefined, { active: false });
    await expect(receiveSharedFiles(fake, 1000)).resolves.toBeNull();
  });

  it("says which file of the share is shown", () => {
    expect(sharedFileNotice(0, 1)).toBe(
      "Fichier partagé : choisissez son type (document ou ticket de caisse) et son espace, puis envoyez-le.",
    );
    expect(sharedFileNotice(1, 3)).toMatch(/^Fichier partagé 2 sur 3 : /);
  });

  it("offers in the share sheet exactly the types the upload accepts", () => {
    expect([...SHARE_ACCEPT].sort()).toEqual(ACCEPTED_FILES.split(",").sort());
    const target = manifest().share_target;
    expect(target).toEqual({
      action: "/partage",
      method: "POST",
      enctype: "multipart/form-data",
      params: { files: [{ name: "file", accept: SHARE_ACCEPT }] },
    });
  });

  it("drops a share no worker took, and asks to pick the file again", () => {
    const posted = POST();
    expect(posted.status).toBe(303);
    expect(posted.headers.get("location")).toBe(SHARE_PAGE_FAILED);
    expect(posted.headers.get("cache-control")).toBe("no-store");
    expect(GET().headers.get("location")).toBe("/documents");
  });
});
