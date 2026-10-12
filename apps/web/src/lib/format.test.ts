import { describe, expect, it } from "vitest";

import { isoToLocalInput, localInputToIso, splitSnippet } from "./format";

describe("splitSnippet", () => {
  it("splits highlighted parts without interpreting HTML", () => {
    expect(splitSnippet("Préparer «l'été» <img src=x> «montagne»")).toEqual([
      { text: "Préparer ", highlight: false },
      { text: "l'été", highlight: true },
      { text: " <img src=x> ", highlight: false },
      { text: "montagne", highlight: true },
    ]);
  });

  it("returns plain text unchanged", () => {
    expect(splitSnippet("rien")).toEqual([{ text: "rien", highlight: false }]);
  });
});

describe("datetime-local conversion", () => {
  it("round-trips local input values", () => {
    const iso = localInputToIso("2026-10-11T08:30");
    expect(iso).not.toBeNull();
    expect(isoToLocalInput(iso)).toBe("2026-10-11T08:30");
  });

  it("maps empty values to null", () => {
    expect(localInputToIso("")).toBeNull();
    expect(isoToLocalInput(null)).toBe("");
  });
});
