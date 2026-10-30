import type { Page } from "@playwright/test";

/**
 * A small WCAG 2.2 AA checker run inside the page, without dependency: axe-core
 * (@axe-core/playwright) is not in pnpm-lock.yaml and could not be added offline. It covers
 * the rules that matter most for this interface; axe can replace it once added (see
 * docs/accessibilite.md). Rules:
 *  - html-lang, document-title, landmark-one-main, page-has-heading-one;
 *  - label (form controls have an accessible name), button-name, link-name;
 *  - duplicate-id, aria-valid-ref (ids named by aria-* attributes exist);
 *  - color-contrast (1.4.3: 4.5:1, 3:1 for large text), placeholders included;
 *  - field-border-contrast (1.4.11: 3:1 between a field's border and what surrounds it);
 *  - target-size (2.5.8: 24 × 24 px, or the spacing exception);
 *  - scrollable-region-focusable (2.1.1).
 */
export interface A11yIssue {
  rule: string;
  target: string;
  detail: string;
}

function auditInPage(): A11yIssue[] {
  const issues: A11yIssue[] = [];
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  type Rgba = [number, number, number, number];

  function describe(el: Element): string {
    const id = el.id ? `#${el.id}` : "";
    const text = (el.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 50);
    return `${el.tagName.toLowerCase()}${id}${text ? ` « ${text} »` : ""}`;
  }

  function report(rule: string, el: Element, detail: string) {
    issues.push({ rule, target: describe(el), detail });
  }

  /** Any CSS colour (oklch, oklab, color-mix results…) as 8-bit RGBA, via a canvas. */
  function rgba(css: string): Rgba | null {
    if (!ctx) return null;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "rgba(0, 0, 0, 0)";
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0, a = 0] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  }

  function blend(top: Rgba, bottom: Rgba): Rgba {
    const a = top[3];
    return [
      top[0] * a + bottom[0] * (1 - a),
      top[1] * a + bottom[1] * (1 - a),
      top[2] * a + bottom[2] * (1 - a),
      1,
    ];
  }

  function luminance([r, g, b]: Rgba): number {
    const lin = (c: number) => {
      const s = c / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }

  function ratio(a: Rgba, b: Rgba): number {
    const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
    return (l1 + 0.05) / (l2 + 0.05);
  }

  /** The opaque colour behind `el`, from its own and its ancestors' backgrounds; null when
   * an image or gradient makes it unknown. */
  function background(el: Element | null): Rgba | null {
    const layers: Rgba[] = [];
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== "none") return null;
      const color = rgba(style.backgroundColor);
      if (color && color[3] > 0) {
        layers.push(color);
        if (color[3] >= 1) break;
      }
    }
    let result: Rgba = [255, 255, 255, 1];
    for (const layer of layers.reverse()) result = blend(layer, result);
    return result;
  }

  function visible(el: Element): boolean {
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const rect = el.getBoundingClientRect();
    // sr-only content (1 × 1 px, clipped) is for screen readers, not seen.
    return rect.width > 1 && rect.height > 1;
  }

  function disabled(el: Element): boolean {
    return Boolean(el.closest(":disabled, [aria-disabled='true']"));
  }

  function nameOf(el: Element): string {
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const text = labelledby
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent ?? "")
        .join(" ")
        .trim();
      if (text) return text;
    }
    const label = el.getAttribute("aria-label")?.trim();
    if (label) return label;
    if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) {
      const text = [...(el.labels ?? [])].map((l) => l.textContent ?? "").join(" ").trim();
      if (text) return text;
    } else {
      const clone = el.cloneNode(true) as Element;
      clone.querySelectorAll("[aria-hidden='true']").forEach((n) => n.remove());
      const text = (clone.textContent ?? "").trim();
      if (text) return text;
      const alt = el.querySelector("img[alt]")?.getAttribute("alt")?.trim();
      if (alt) return alt;
    }
    return el.getAttribute("title")?.trim() ?? "";
  }

  // Document.
  if (document.documentElement.lang !== "fr") report("html-lang", document.documentElement, "lang ≠ fr");
  if (!document.title.trim()) report("document-title", document.documentElement, "titre vide");
  const mains = document.querySelectorAll("main");
  if (mains.length !== 1) report("landmark-one-main", document.body, `${mains.length} <main>`);
  const h1 = [...document.querySelectorAll("h1")].filter(visible);
  if (h1.length !== 1) report("page-has-heading-one", document.body, `${h1.length} h1 visibles`);

  // Ids and references.
  const ids = new Map<string, number>();
  document.querySelectorAll("[id]").forEach((el) => ids.set(el.id, (ids.get(el.id) ?? 0) + 1));
  for (const [id, count] of ids) {
    if (count > 1) report("duplicate-id", document.getElementById(id) ?? document.body, `id « ${id} » ×${count}`);
  }
  for (const attr of ["aria-labelledby", "aria-describedby", "aria-controls"]) {
    document.querySelectorAll(`[${attr}]`).forEach((el) => {
      for (const id of (el.getAttribute(attr) ?? "").split(/\s+/).filter(Boolean)) {
        if (!document.getElementById(id)) report("aria-valid-ref", el, `${attr} → « ${id} » absent`);
      }
    });
  }

  // Names.
  const controls = document.querySelectorAll<HTMLElement>(
    "input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea",
  );
  // Rendered elements, sr-only ones included (they are in the accessibility tree).
  const rendered = (el: Element) => el.checkVisibility({ checkVisibilityCSS: true }) && !el.closest("[aria-hidden='true']");
  controls.forEach((el) => {
    if (rendered(el) && !nameOf(el)) report("label", el, "champ sans nom accessible");
  });
  document.querySelectorAll("button, [role=button], [role=tab], [role=switch]").forEach((el) => {
    if (rendered(el) && !nameOf(el)) report("button-name", el, "bouton sans nom");
  });
  document.querySelectorAll("a[href]").forEach((el) => {
    if (rendered(el) && !nameOf(el)) report("link-name", el, "lien sans nom");
  });

  // Text contrast: every visible element with its own text.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set<Element>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node.parentElement;
    if (!el || seen.has(el) || !(node.textContent ?? "").trim()) continue;
    seen.add(el);
    if (["SCRIPT", "STYLE", "TITLE", "NOSCRIPT", "OPTION"].includes(el.tagName)) continue;
    if (!visible(el) || disabled(el)) continue;
    const style = getComputedStyle(el);
    const fg = rgba(style.color);
    const bg = background(el);
    if (!fg || !bg) continue;
    const size = Number.parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    const needed = large ? 3 : 4.5;
    const value = ratio(blend(fg, bg), bg);
    if (value < needed - 0.005) report("color-contrast", el, `${value.toFixed(2)}:1 < ${needed}:1`);
  }
  // Placeholders.
  document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input[placeholder], textarea[placeholder]").forEach((el) => {
    if (!visible(el) || disabled(el) || el.value) return;
    const fg = rgba(getComputedStyle(el, "::placeholder").color);
    const bg = background(el);
    if (!fg || !bg) return;
    const value = ratio(blend(fg, bg), bg);
    if (value < 4.495) report("color-contrast", el, `placeholder ${value.toFixed(2)}:1 < 4.5:1`);
  });

  // Field borders against the field and against what surrounds it.
  document
    .querySelectorAll<HTMLElement>(
      "input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=hidden]):not([type=range]), select, textarea",
    )
    .forEach((el) => {
      if (!visible(el) || disabled(el)) return;
      const style = getComputedStyle(el);
      if (Number.parseFloat(style.borderBottomWidth) === 0) return;
      const border = rgba(style.borderBottomColor);
      const inside = background(el);
      const outside = background(el.parentElement);
      if (!border || !inside || !outside) return;
      const value = Math.max(ratio(blend(border, outside), outside), ratio(blend(border, inside), inside));
      if (value < 2.995) report("field-border-contrast", el, `bordure ${value.toFixed(2)}:1 < 3:1`);
    });

  // Target size (2.5.8): 24 × 24 px, or a 24 px circle that touches no other target.
  const targets = [
    ...document.querySelectorAll<HTMLElement>(
      "button, [role=button], [role=tab], select, input[type=checkbox], input[type=radio], a[href]",
    ),
  ].filter((el) => visible(el) && !disabled(el));
  const boxes = targets.map((el) => {
    // A checkbox counts with the label that wraps it (CheckboxTarget): the whole label taps it.
    const label = el instanceof HTMLInputElement ? el.closest("label") : null;
    return { el, rect: (label ?? el).getBoundingClientRect() };
  });
  const distance = (x: number, y: number, r: DOMRect) =>
    Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom));
  for (const box of boxes) {
    const { el, rect } = box;
    if (rect.width >= 23.5 && rect.height >= 23.5) continue;
    // Inline links in a sentence are exempt (2.5.8 « Inline »).
    if (el.tagName === "A" && getComputedStyle(el).display === "inline") continue;
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const crowded = boxes.some((other) => {
      if (other === box || other.el.contains(el) || el.contains(other.el)) return false;
      const small = other.rect.width < 23.5 || other.rect.height < 23.5;
      const ox = other.rect.left + other.rect.width / 2;
      const oy = other.rect.top + other.rect.height / 2;
      return distance(x, y, other.rect) < 12 || (small && Math.hypot(x - ox, y - oy) < 24);
    });
    if (crowded) report("target-size", el, `${Math.round(rect.width)} × ${Math.round(rect.height)} px, cible voisine trop proche`);
  }

  // Scrollable regions reachable by keyboard.
  document.querySelectorAll<HTMLElement>("body *").forEach((el) => {
    const style = getComputedStyle(el);
    const scrollsX = /(auto|scroll)/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1;
    const scrollsY = /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
    if (!(scrollsX || scrollsY) || !visible(el)) return;
    const focusable = el.tabIndex >= 0 || el.querySelector("a[href], button, input, select, textarea, [tabindex]");
    if (!focusable) report("scrollable-region-focusable", el, "zone défilante inatteignable au clavier");
  });

  return issues;
}

/**
 * Accepted exceptions, each with its reason (rule + start of the target description).
 * Keep this list empty unless a check is a proven false positive.
 */
export const EXCEPTIONS: { rule: string; target: RegExp; reason: string }[] = [];

export async function audit(page: Page): Promise<A11yIssue[]> {
  const issues = await page.evaluate(auditInPage);
  return issues.filter((issue) => !EXCEPTIONS.some((e) => e.rule === issue.rule && e.target.test(issue.target)));
}

/** A readable list for the assertion message. */
export function formatIssues(issues: A11yIssue[]): string {
  return issues.map((i) => `${i.rule} : ${i.target} — ${i.detail}`).join("\n");
}
