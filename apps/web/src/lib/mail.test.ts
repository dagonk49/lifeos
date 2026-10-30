import { describe, expect, it } from "vitest";

import {
  activeContentList,
  draftErrors,
  formatSize,
  isValidAddress,
  labelErrorMessage,
  parseAddresses,
  replySubject,
  senderLabel,
  subjectLabel,
} from "./mail";

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

describe("compose helpers", () => {
  it("reads the addresses of a field, once each, in lower case", () => {
    expect(parseAddresses(" Lea@Example.org, sam@example.org;lea@example.org  x@y.fr ")).toEqual([
      "lea@example.org",
      "sam@example.org",
      "x@y.fr",
    ]);
    expect(parseAddresses("  ")).toEqual([]);
    expect(isValidAddress("lea@example.org")).toBe(true);
    expect(isValidAddress("pas une adresse")).toBe(false);
    expect(isValidAddress("a@b")).toBe(false);
  });

  it("validates the form like the server", () => {
    const fields = { to: "lea@example.org", cc: "", bcc: "", subject: "Bonjour", body: "" };
    expect(draftErrors(fields, { sending: true })).toEqual({});
    expect(draftErrors({ ...fields, cc: "oops" }, { sending: false }).cc).toBe("Adresse non valide : oops");
    expect(draftErrors({ ...fields, subject: "a\nb" }, { sending: false }).subject).toContain("une ligne");
    expect(draftErrors({ ...fields, to: "" }, { sending: false })).toEqual({});
    expect(draftErrors({ ...fields, to: "" }, { sending: true }).to).toBe("Indiquez au moins un destinataire.");
    expect(draftErrors({ ...fields, subject: "", body: " " }, { sending: true }).body).toBe(
      "Écrivez un objet ou un message.",
    );
    const many = Array.from({ length: 51 }, (_, i) => `p${i}@example.org`).join(",");
    expect(draftErrors({ ...fields, to: many }, { sending: false }).to).toBe("50 destinataires au plus.");
  });

  it("prefixes a reply subject once", () => {
    expect(replySubject("Sortie")).toBe("Re: Sortie");
    expect(replySubject("RE : Sortie")).toBe("RE : Sortie");
  });
});
