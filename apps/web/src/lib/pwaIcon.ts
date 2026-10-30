import { deflateSync } from "node:zlib";

/**
 * PNG icons of the installed app (manifest, Apple touch icon), drawn from the same design
 * as src/app/icon.svg: a white "L" stroke on teal. Generated on the server (no binary file
 * in the repository, no public/ directory in the image).
 *
 * - "rounded": the favicon's rounded square, transparent corners (manifest "any");
 * - "full": a full-bleed square (manifest "maskable" and Apple, which round it themselves).
 *   The stroke stays inside the maskable safe zone (a circle of 40 % of the size).
 */
export type IconShape = "rounded" | "full";

export interface IconSpec {
  size: number;
  shape: IconShape;
}

/** Served at /icons/<name> (src/app/icons/[name]/route.ts). */
export const ICONS: Record<string, IconSpec> = {
  "icon-192.png": { size: 192, shape: "rounded" },
  "icon-512.png": { size: 512, shape: "rounded" },
  "icon-maskable-512.png": { size: 512, shape: "full" },
  "apple-touch-icon.png": { size: 180, shape: "full" },
};

const TEAL: [number, number, number] = [0x0f, 0x76, 0x6e];
const WHITE: [number, number, number] = [0xff, 0xff, 0xff];
// In the 32-unit space of icon.svg: path M10 8 v16 h12, stroke 3.5, rx 7.
const SEGMENTS: [number, number, number, number][] = [
  [10, 8, 10, 24],
  [10, 24, 22, 24],
];
const HALF_STROKE = 1.75;
const RADIUS = 7;
const SAMPLES = 4;

function segmentDistance(px: number, py: number, [ax, ay, bx, by]: [number, number, number, number]): number {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function insideRoundedSquare(x: number, y: number): boolean {
  const cx = Math.min(Math.max(x, RADIUS), 32 - RADIUS);
  const cy = Math.min(Math.max(y, RADIUS), 32 - RADIUS);
  return Math.hypot(x - cx, y - cy) <= RADIUS;
}

/** RGBA pixels, antialiased by supersampling. */
export function renderPixels({ size, shape }: IconSpec): Uint8Array {
  const pixels = new Uint8Array(size * size * 4);
  const unit = 32 / size;
  for (let row = 0; row < size; row++) {
    for (let col = 0; col < size; col++) {
      let background = 0;
      let stroke = 0;
      for (let sy = 0; sy < SAMPLES; sy++) {
        for (let sx = 0; sx < SAMPLES; sx++) {
          const x = (col + (sx + 0.5) / SAMPLES) * unit;
          const y = (row + (sy + 0.5) / SAMPLES) * unit;
          if (shape === "rounded" && !insideRoundedSquare(x, y)) continue;
          background++;
          if (SEGMENTS.some((s) => segmentDistance(x, y, s) <= HALF_STROKE)) stroke++;
        }
      }
      const offset = (row * size + col) * 4;
      const covered = background / (SAMPLES * SAMPLES);
      const mix = background === 0 ? 0 : stroke / background;
      for (let c = 0; c < 3; c++) {
        pixels[offset + c] = Math.round(TEAL[c]! * (1 - mix) + WHITE[c]! * mix);
      }
      pixels[offset + 3] = Math.round(255 * covered);
    }
  }
  return pixels;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Uint8Array): Uint8Array {
  const out = new Uint8Array(12 + data.length);
  const view = new DataView(out.buffer);
  view.setUint32(0, data.length);
  const body = new Uint8Array(4 + data.length);
  body.set(new TextEncoder().encode(type), 0);
  body.set(data, 4);
  out.set(body, 4);
  view.setUint32(8 + data.length, crc32(body));
  return out;
}

/** A PNG file (RGBA, 8 bits per channel, no interlacing). */
export function encodePng(size: number, pixels: Uint8Array): Uint8Array {
  const header = new Uint8Array(13);
  const view = new DataView(header.buffer);
  view.setUint32(0, size);
  view.setUint32(4, size);
  header.set([8, 6, 0, 0, 0], 8);
  const rows = new Uint8Array(size * (size * 4 + 1));
  for (let row = 0; row < size; row++) {
    rows[row * (size * 4 + 1)] = 0; // filter: none
    rows.set(pixels.subarray(row * size * 4, (row + 1) * size * 4), row * (size * 4 + 1) + 1);
  }
  const parts = [
    new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", new Uint8Array(deflateSync(rows))),
    chunk("IEND", new Uint8Array()),
  ];
  const png = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const part of parts) {
    png.set(part, at);
    at += part.length;
  }
  return png;
}

const cache = new Map<string, Uint8Array>();

/** The PNG of a named icon (null: unknown name), computed once per process. */
export function iconPng(name: string): Uint8Array | null {
  const spec = ICONS[name];
  if (!spec) return null;
  let png = cache.get(name);
  if (!png) {
    png = encodePng(spec.size, renderPixels(spec));
    cache.set(name, png);
  }
  return png;
}
