import { describe, expect, it } from "vitest";

import { activeContentList, formatSize, labelErrorMessage, senderLabel, subjectLabel } from "./mail";

describe("mail helpers", () => {
  it("labels the sender and the subject", () => {
    expect(senderLabel({ from_name: "Alice", from_address: "alice@example.org" })).toBe("Alice <alice@example.org>");
    expect(senderLabel({ from_name: null, from_address: "alice@example.org" })).toBe("alice@example.org");
    expect(senderLabel({ from_name: null, from_address: null })).toBe("Expéditeur inconnu");
    expect(subjectLabel("  ")).toBe("(sans objet)");
  });

  it("formats sizes and active content in French", () => {
    expect(formatSize(512)).toBe("512 o");
    expect(formatSize(2048)).toBe("2 Ko");
    expect(formatSize(3 * 1024 * 1024 + 100_000)).toBe("3,1 Mo");
    expect(activeContentList([])).toBeNull();
    expect(activeContentList(["open_action", "unknown"])).toBe("action à l'ouverture, unknown");
  });

  it("explains why a label stopped syncing", () => {
    expect(labelErrorMessage(null)).toBeNull();
    expect(labelErrorMessage("consent_missing")).toContain("non autorisée dans cet espace");
    expect(labelErrorMessage("http_503")).toContain("nouvel essai automatique");
  });
});
