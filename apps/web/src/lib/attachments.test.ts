import { describe, expect, it } from "vitest";

import { attachmentState, attachmentStateLabel, attachmentsReading, sendableAttachments } from "./attachments";
import type { DocumentSummary } from "./types";

function doc(id: string, status: DocumentSummary["status"], sensitivity: DocumentSummary["sensitivity"] = []) {
  return { id, status, sensitivity } as DocumentSummary;
}

describe("attachments", () => {
  it("classifies attached documents", () => {
    expect(attachmentState(undefined)).toBe("reading");
    expect(attachmentState(doc("a", "pending"))).toBe("reading");
    expect(attachmentState(doc("a", "processing"))).toBe("reading");
    expect(attachmentState(doc("a", "ready"))).toBe("ready");
    expect(attachmentState(doc("a", "ready", ["bank"]))).toBe("sensitive");
    expect(attachmentState(doc("a", "failed"))).toBe("unreadable");
  });

  it("sends only ready, non-sensitive documents and waits for those being read", () => {
    const docs = [doc("a", "ready"), doc("b", "ready", ["health"]), doc("c", "failed"), undefined];
    expect(sendableAttachments(docs)).toEqual(["a"]);
    expect(attachmentsReading(docs)).toBe(true);
    expect(attachmentsReading(docs.slice(0, 3))).toBe(false);
  });

  it("says why a document stays on the server", () => {
    expect(attachmentStateLabel("sensitive", ["identity", "bank"])).toBe(
      "Document sensible (identité, banque) : jamais envoyé à l'IA",
    );
    expect(attachmentStateLabel("reading")).toBe("Lecture locale en cours…");
  });
});
