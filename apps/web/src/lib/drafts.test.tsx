import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useSessionDraft } from "./drafts";
import { clearDrafts, DRAFT_KEYS } from "./session";

let setter: (value: string) => void = () => {};

function Composer() {
  const [draft, setDraft] = useSessionDraft(DRAFT_KEYS.chat);
  setter = setDraft;
  return <p>draft:{draft}</p>;
}

describe("useSessionDraft", () => {
  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
  });

  it("keeps the text in this tab until it is emptied", async () => {
    render(<Composer />);
    act(() => setter("Bonjour"));
    expect(window.sessionStorage.getItem(DRAFT_KEYS.chat)).toBe("Bonjour");
    cleanup();
    // A new page (after signing in again): the draft is back.
    render(<Composer />);
    expect(await screen.findByText("draft:Bonjour")).toBeTruthy();
    act(() => setter(""));
    expect(window.sessionStorage.getItem(DRAFT_KEYS.chat)).toBeNull();
  });

  it("is forgotten on sign-out", () => {
    window.sessionStorage.setItem(DRAFT_KEYS.journal, "Ma journée");
    clearDrafts();
    expect(window.sessionStorage.getItem(DRAFT_KEYS.journal)).toBeNull();
  });
});
