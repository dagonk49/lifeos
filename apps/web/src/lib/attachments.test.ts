import { describe, expect, it } from "vitest";

import {
  attachmentPollInterval,
  attachmentState,
  attachmentStateLabel,
  attachmentsReading,
  sendableAttachments,
} from "./attachments";
import type { DocumentSummary } from "./types";

function doc(
  status: DocumentSummary["status"],
  sensitivity: DocumentSummary["sensitivity"] = [],
  error: string | null = null,
) {
  return { status, sensitivity, error } as DocumentSummary;
}

describe("attachments", () => {
  it("classifies attached documents", () => {
    expect(attachmentState(undefined)).toBe("reading");
    expect(attachmentState(doc("pending"))).toBe("reading");
    expect(attachmentState(doc("processing"))).toBe("reading");
    expect(attachmentState(doc("pending", [], "extraction_unavailable"))).toBe("postponed");
    expect(attachmentState(doc("ready"))).toBe("ready");
    expect(attachmentState(doc("ready", ["bank"]))).toBe("sensitive");
    // « Toujours garder en local »: the owner's choice, whatever the detection says.
    expect(attachmentState({ ...doc("ready"), local_only: true })).toBe("local");
    expect(attachmentState({ ...doc("ready", ["health"]), local_only: true })).toBe("sensitive");
    expect(attachmentState({ ...doc("pending"), local_only: true })).toBe("reading");
    expect(attachmentState(doc("failed"))).toBe("unreadable");
    expect(attachmentState(doc("ready"), true)).toBe("unavailable");
    expect(attachmentState(undefined, true)).toBe("unavailable");
  });

  it("sends only ready, non-sensitive documents; only a reading one holds the message", () => {
    const ids = ["a", "b", "c", "d", "e"];
    const states = [
      attachmentState(doc("ready")),
      attachmentState(doc("ready", ["health"])),
      attachmentState(doc("failed")),
      attachmentState(doc("pending", [], "extraction_unavailable")),
      attachmentState(undefined, true),
    ];
    expect(sendableAttachments(ids, states)).toEqual(["a"]);
    expect(attachmentsReading(states)).toBe(false);
    expect(attachmentsReading([...states, attachmentState(doc("processing"))])).toBe(true);
  });

  it("polls fast while reading, slowly when postponed, never once settled", () => {
    expect(attachmentPollInterval("reading")).toBe(1500);
    expect(attachmentPollInterval("postponed")).toBe(10_000);
    for (const state of ["ready", "sensitive", "local", "unreadable", "unavailable"] as const) {
      expect(attachmentPollInterval(state)).toBe(false);
    }
  });

  it("says why a document stays on the server", () => {
    expect(attachmentStateLabel("sensitive", ["identity", "bank"])).toBe(
      "Document sensible (identité, banque) : jamais envoyé à l'IA",
    );
    expect(attachmentStateLabel("local")).toBe("Gardé en local (votre choix) : jamais envoyé à l'IA");
    expect(attachmentStateLabel("reading")).toBe("Lecture locale en cours…");
    expect(attachmentStateLabel("unavailable")).toMatch(/introuvable/);
  });
});
