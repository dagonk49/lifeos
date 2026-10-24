import { describe, expect, it } from "vitest";

import { buildReview, maskedSummary, maskedTotal, refusalText, withoutAttachment } from "./privacy";
import type { SendPreview } from "./types";

const docs = [
  { title: "Courrier", sensitivity: [] },
  { title: "RIB", sensitivity: [] },
  { title: "Carte", sensitivity: ["identity" as const] },
  { title: "Notes", sensitivity: [] },
];

function preview(overrides: Partial<SendPreview> = {}): SendPreview {
  return {
    masked: {},
    attachments: [
      { document_id: "a", sent: true, reason: null, sensitivity: [], masked: { phone: 1 } },
      { document_id: "b", sent: false, reason: "sensitive", sensitivity: ["bank"], masked: {} },
    ],
    ...overrides,
  };
}

describe("privacy", () => {
  it("counts and names masked identifiers by kind, never their values", () => {
    expect(maskedTotal({})).toBe(0);
    expect(maskedTotal({ iban: 1, phone: 2 })).toBe(3);
    expect(maskedSummary({})).toBe("");
    expect(maskedSummary({ phone: 2, iban: 1 })).toBe("1 IBAN, 2 numéros de téléphone");
    expect(maskedSummary({ secret: 1, tax_number: 2 })).toBe("2 numéros fiscaux, 1 mot de passe ou code secret");
    expect(maskedSummary({ email: 1, id_number: 1, passport: 0 })).toBe(
      "1 numéro de pièce d'identité, 1 adresse e-mail",
    );
  });

  it("says why a document stays on the server", () => {
    expect(refusalText("sensitive", ["health", "bank"])).toBe("document sensible (santé, banque)");
    expect(refusalText("local_only")).toBe("gardé en local à votre demande");
    expect(refusalText("not_allowed")).toMatch(/agent n'a pas accès/);
  });

  it("merges what the page knows with the server's decision, in the order attached", () => {
    const review = buildReview(
      ["a", "b", "c", "d"],
      ["ready", "ready", "sensitive", "local"],
      docs,
      preview({ masked: { iban: 1 } }),
    );
    expect(review.items).toEqual([
      { id: "a", title: "Courrier", sent: true, detail: "1 numéro de téléphone" },
      { id: "b", title: "RIB", sent: false, detail: "document sensible (banque)" },
      { id: "c", title: "Carte", sent: false, detail: "document sensible (identité)" },
      { id: "d", title: "Notes", sent: false, detail: "gardé en local à votre demande" },
    ]);
    expect(review.attachments).toEqual(["a"]);
    expect(review.needed).toBe(true);
  });

  it("needs no confirmation when nothing is masked or left out", () => {
    const clean = buildReview(["a"], ["ready"], docs, {
      masked: {},
      attachments: [{ document_id: "a", sent: true, reason: null, sensitivity: [], masked: {} }],
    });
    expect(clean).toEqual({
      masked: {},
      items: [{ id: "a", title: "Courrier", sent: true, detail: "" }],
      attachments: ["a"],
      needed: false,
    });
    // A document the server did not answer for is not sent.
    expect(buildReview(["z"], ["ready"], [undefined], { masked: {}, attachments: [] }).items).toEqual([
      { id: "z", title: "Document", sent: false, detail: "document introuvable" },
    ]);
  });

  it("updates the review when the owner removes an attachment", () => {
    const review = buildReview(["a", "b"], ["ready", "ready"], docs, preview());
    const withoutRib = withoutAttachment(review, "b");
    expect(withoutRib.items.map((i) => i.id)).toEqual(["a"]);
    expect(withoutRib.needed).toBe(true); // the letter's phone number is still masked
    const empty = withoutAttachment(withoutRib, "a");
    expect(empty).toEqual({ masked: {}, items: [], attachments: [], needed: false });
  });
});
