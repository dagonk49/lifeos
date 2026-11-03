/**
 * WCAG contrast of CSS colours, for the Tailwind v4 palette (OKLCH) and plain hex colours.
 * Used by src/lib/contrast.test.ts to keep the design tokens at AA (1.4.3 and 1.4.11).
 */

export type Rgb = [number, number, number];

const OKLCH = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)\s*\)$/;

function encode(linear: number): number {
  const c = Math.min(1, Math.max(0, linear));
  return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
}

function decode(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

/** An `oklch(L C H)` or `#rrggbb` / `#rgb` colour as 8-bit sRGB (clipped to the gamut). */
export function parseColor(css: string): Rgb {
  const value = css.trim();
  const hex = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(value)?.[1];
  if (hex) {
    const full = hex.length === 3 ? [...hex].map((c) => c + c).join("") : hex;
    return [0, 2, 4].map((i) => Number.parseInt(full.slice(i, i + 2), 16)) as Rgb;
  }
  const match = OKLCH.exec(value);
  if (!match) throw new Error(`unsupported colour: ${css}`);
  const lightness = Number(match[1]) / (match[2] === "%" ? 100 : 1);
  const chroma = Number(match[3]);
  const hue = (Number(match[4]) * Math.PI) / 180;
  const a = chroma * Math.cos(hue);
  const b = chroma * Math.sin(hue);
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const linear = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return linear.map((c) => Math.round(encode(c) * 255)) as Rgb;
}

/** `top` painted with `alpha` over `bottom` (e.g. Tailwind's bg-teal-900/50). */
export function over(top: Rgb, alpha: number, bottom: Rgb): Rgb {
  return top.map((c, i) => Math.round(c * alpha + (bottom[i] ?? 0) * (1 - alpha))) as Rgb;
}

export function luminance([r, g, b]: Rgb): number {
  return 0.2126 * decode(r / 255) + 0.7152 * decode(g / 255) + 0.0722 * decode(b / 255);
}

/** The WCAG contrast ratio, from 1 to 21. */
export function contrast(a: Rgb, b: Rgb): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}
