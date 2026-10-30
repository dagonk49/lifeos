import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { Field, Input, PageHeader, Spinner, Tabs } from "./ui";

afterEach(cleanup);

describe("Field", () => {
  it("names the control by its visible label and describes it by its hint", () => {
    render(
      <Field label="Montant" hint="En euros, avec deux décimales.">
        <Input aria-describedby="other" />
      </Field>,
    );
    const input = screen.getByLabelText("Montant");
    const ids = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids[0]).toBe("other");
    expect(document.getElementById(ids[1] ?? "")?.textContent).toBe("En euros, avec deux décimales.");
  });

  it("adds no description without a hint", () => {
    render(
      <Field label="Titre">
        <Input />
      </Field>,
    );
    expect(screen.getByLabelText("Titre").getAttribute("aria-describedby")).toBeNull();
  });
});

function TabsDemo() {
  const [tab, setTab] = useState<"a" | "b" | "c">("a");
  return (
    <Tabs
      label="Type"
      tabs={[
        { id: "a", label: "Faits" },
        { id: "b", label: "Souvenirs" },
        { id: "c", label: "À vérifier" },
      ]}
      value={tab}
      onChange={setTab}
    >
      <p>Contenu {tab}</p>
    </Tabs>
  );
}

describe("Tabs", () => {
  it("links each tab to its panel and keeps only the selected tab in the tab order", () => {
    render(<TabsDemo />);
    const list = screen.getByRole("tablist", { name: "Type" });
    const [first, second] = screen.getAllByRole("tab");
    expect(list).toBeTruthy();
    expect(first?.getAttribute("aria-selected")).toBe("true");
    expect(first?.tabIndex).toBe(0);
    expect(second?.tabIndex).toBe(-1);
    const panel = screen.getByRole("tabpanel", { name: "Faits" });
    expect(first?.getAttribute("aria-controls")).toBe(panel.id);
    expect(panel.textContent).toBe("Contenu a");
  });

  it("moves with the arrow keys (wrapping), Home and End", () => {
    render(<TabsDemo />);
    const tab = (name: string) => screen.getByRole("tab", { name });
    tab("Faits").focus();
    fireEvent.keyDown(tab("Faits"), { key: "ArrowRight" });
    expect(tab("Souvenirs").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab("Souvenirs"));
    expect(screen.getByRole("tabpanel", { name: "Souvenirs" }).textContent).toBe("Contenu b");

    fireEvent.keyDown(tab("Souvenirs"), { key: "End" });
    expect(document.activeElement).toBe(tab("À vérifier"));
    fireEvent.keyDown(tab("À vérifier"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(tab("Faits"));
    fireEvent.keyDown(tab("Faits"), { key: "ArrowLeft" });
    expect(tab("À vérifier").getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tab("À vérifier"), { key: "Home" });
    expect(tab("Faits").getAttribute("aria-selected")).toBe("true");
    expect(tab("À vérifier").tabIndex).toBe(-1);
  });
});

describe("PageHeader", () => {
  it("gives the page its own document title", async () => {
    render(<PageHeader title="Aujourd'hui, mercredi 28 octobre" documentTitle="Accueil" />);
    expect(screen.getByRole("heading", { level: 1, name: "Aujourd'hui, mercredi 28 octobre" })).toBeTruthy();
    await waitFor(() => expect(document.title).toBe("Accueil · LifeOS"));
  });
});

describe("Spinner", () => {
  it("stops spinning when reduced motion is asked for", () => {
    render(<Spinner />);
    expect(screen.getByRole("status").querySelector(".animate-spin")?.className).toContain("motion-reduce:animate-none");
  });
});
