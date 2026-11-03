import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

import { describe, expect, it } from "vitest";

import { contrast, over, parseColor, type Rgb } from "./contrast";

// The palette is read from the installed Tailwind version: an upgrade that darkens or
// lightens a shade is caught here.
const theme = readFileSync(createRequire(import.meta.url).resolve("tailwindcss/theme.css"), "utf8");

function color(name: string): Rgb {
  const value = new RegExp(`--color-${name}:\\s*([^;]+);`).exec(theme)?.[1];
  if (!value) throw new Error(`--color-${name} missing from tailwindcss/theme.css`);
  return parseColor(value);
}

const white = color("white");
// Light surfaces: cards (white) and the page (stone-50). Dark: cards (stone-900), the page
// (stone-950) and hovered rows (stone-800).
const LIGHT = { card: white, page: color("stone-50") };
const DARK = { card: color("stone-900"), page: color("stone-950"), hover: color("stone-800") };

const TEXT = 4.5;
const NON_TEXT = 3;

describe("contrast helpers", () => {
  it("matches the WCAG reference values", () => {
    expect(contrast(parseColor("#000"), parseColor("#fff"))).toBeCloseTo(21, 5);
    expect(contrast(parseColor("#777777"), white)).toBeCloseTo(4.48, 2);
    expect(parseColor("oklch(100% 0 0)")).toEqual([255, 255, 255]);
  });
});

describe("design tokens (WCAG 2.2 AA)", () => {
  it("text-muted is readable on every surface, in both themes", () => {
    for (const surface of Object.values(LIGHT)) expect(contrast(color("stone-500"), surface)).toBeGreaterThanOrEqual(TEXT);
    for (const surface of Object.values(DARK)) expect(contrast(color("stone-400"), surface)).toBeGreaterThanOrEqual(TEXT);
  });

  it("plain stone-500 fails on a dark card (the reason for text-muted)", () => {
    expect(contrast(color("stone-500"), DARK.card)).toBeLessThan(TEXT);
  });

  it("body, label and secondary text", () => {
    expect(contrast(color("stone-900"), LIGHT.page)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("stone-100"), DARK.page)).toBeGreaterThanOrEqual(TEXT);
    // Field labels, nav links, notes: stone-700 / stone-600, dark stone-300 / stone-400.
    for (const shade of ["stone-600", "stone-700"]) {
      for (const surface of Object.values(LIGHT)) expect(contrast(color(shade), surface)).toBeGreaterThanOrEqual(TEXT);
    }
    for (const shade of ["stone-300", "stone-400"]) {
      for (const surface of Object.values(DARK)) expect(contrast(color(shade), surface)).toBeGreaterThanOrEqual(TEXT);
    }
  });

  it("placeholders and field borders", () => {
    expect(contrast(color("stone-500"), white)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("stone-400"), DARK.card)).toBeGreaterThanOrEqual(TEXT);
    // A field's border against the field and against the page around it (1.4.11).
    for (const surface of [white, LIGHT.page, DARK.card, DARK.page]) {
      expect(contrast(color("stone-500"), surface)).toBeGreaterThanOrEqual(NON_TEXT);
    }
  });

  it("the focus outline stands out in both themes", () => {
    for (const surface of [...Object.values(LIGHT), ...Object.values(DARK)]) {
      expect(contrast(color("teal-600"), surface)).toBeGreaterThanOrEqual(NON_TEXT);
    }
  });

  it("buttons, links and the active navigation link", () => {
    expect(contrast(white, color("teal-700"))).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(white, color("teal-800"))).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(white, color("red-700"))).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("teal-800"), white)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("teal-300"), DARK.page)).toBeGreaterThanOrEqual(TEXT);
    // Links and « Enregistré » confirmations: teal-700, dark teal-400.
    expect(contrast(color("teal-700"), LIGHT.page)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("teal-400"), DARK.card)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("teal-900"), color("teal-100"))).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("teal-100"), over(color("teal-900"), 0.5, DARK.page))).toBeGreaterThanOrEqual(TEXT);
  });

  it("badges in every tone", () => {
    const tones: [string, string][] = [
      ["teal", "teal"],
      ["amber", "amber"],
      ["red", "red"],
      ["blue", "sky"],
      ["violet", "violet"],
    ];
    for (const [, hue] of tones) {
      expect(contrast(color(`${hue}-800`), color(`${hue}-100`))).toBeGreaterThanOrEqual(TEXT);
      expect(contrast(color(`${hue}-200`), over(color(`${hue}-900`), 0.5, DARK.card))).toBeGreaterThanOrEqual(TEXT);
    }
    expect(contrast(color("stone-700"), color("stone-100"))).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("stone-300"), color("stone-800"))).toBeGreaterThanOrEqual(TEXT);
  });

  it("alerts and status text", () => {
    for (const hue of ["sky", "red", "amber", "teal"]) {
      expect(contrast(color(`${hue}-900`), color(`${hue}-50`))).toBeGreaterThanOrEqual(TEXT);
      expect(contrast(color(`${hue}-100`), color(`${hue}-950`))).toBeGreaterThanOrEqual(TEXT);
    }
    // Overdue dates and errors (red-700 / red-400), hints (amber-800 / amber-300).
    expect(contrast(color("red-700"), white)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("red-400"), DARK.card)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("amber-800"), white)).toBeGreaterThanOrEqual(TEXT);
    expect(contrast(color("amber-300"), DARK.card)).toBeGreaterThanOrEqual(TEXT);
  });
});
