// @vitest-environment node
import { inflateSync } from "node:zlib";

import { describe, expect, it } from "vitest";

import { GET } from "@/app/icons/[name]/route";
import manifest from "@/app/manifest";

import { crc32, ICONS, iconPng, renderPixels } from "./pwaIcon";

function readPng(png: Uint8Array) {
  const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
  expect(Array.from(png.subarray(0, 8))).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const chunks: { type: string; data: Uint8Array }[] = [];
  let at = 8;
  while (at < png.length) {
    const length = view.getUint32(at);
    const type = new TextDecoder().decode(png.subarray(at + 4, at + 8));
    const data = png.subarray(at + 8, at + 8 + length);
    expect(view.getUint32(at + 8 + length)).toBe(crc32(png.subarray(at + 4, at + 8 + length)));
    chunks.push({ type, data });
    at += 12 + length;
  }
  return chunks;
}

describe("PWA icons", () => {
  it("computes the CRC-32 of PNG chunks", () => {
    expect(crc32(new TextEncoder().encode("IEND"))).toBe(0xae426082);
  });

  it("are valid RGBA PNGs of the announced sizes", () => {
    for (const [name, spec] of Object.entries(ICONS)) {
      const chunks = readPng(iconPng(name)!);
      expect(chunks.map((c) => c.type)).toEqual(["IHDR", "IDAT", "IEND"]);
      const header = new DataView(chunks[0]!.data.buffer, chunks[0]!.data.byteOffset);
      expect([header.getUint32(0), header.getUint32(4)]).toEqual([spec.size, spec.size]);
      expect(Array.from(chunks[0]!.data.subarray(8))).toEqual([8, 6, 0, 0, 0]);
      expect(inflateSync(chunks[1]!.data).length).toBe(spec.size * (spec.size * 4 + 1));
    }
    expect(iconPng("../secret")).toBeNull();
  });

  it("draws the white stroke on teal; maskable and Apple icons have no transparent corner", () => {
    const pixel = (pixels: Uint8Array, size: number, x: number, y: number) =>
      Array.from(pixels.subarray((y * size + x) * 4, (y * size + x) * 4 + 4));
    const rounded = renderPixels({ size: 64, shape: "rounded" });
    expect(pixel(rounded, 64, 0, 0)[3]).toBe(0);
    expect(pixel(rounded, 64, 32, 10)).toEqual([0x0f, 0x76, 0x6e, 255]);
    expect(pixel(rounded, 64, 20, 30)).toEqual([255, 255, 255, 255]); // on the stroke (x=10)
    const full = renderPixels({ size: 64, shape: "full" });
    expect(pixel(full, 64, 0, 0)).toEqual([0x0f, 0x76, 0x6e, 255]);
  });

  it("are served as images, and every manifest icon exists", async () => {
    const response = await GET(new Request("http://localhost/icons/icon-192.png"), {
      params: Promise.resolve({ name: "icon-192.png" }),
    });
    expect(response.headers.get("content-type")).toBe("image/png");
    const missing = await GET(new Request("http://localhost/icons/x.png"), {
      params: Promise.resolve({ name: "x.png" }),
    });
    expect(missing.status).toBe(404);
    const app = manifest();
    expect(app.display).toBe("standalone");
    expect(app.start_url).toBe("/");
    for (const icon of app.icons ?? []) {
      const name = icon.src.replace("/icons/", "");
      expect(ICONS[name], icon.src).toBeDefined();
      expect(icon.sizes).toBe(`${ICONS[name]!.size}x${ICONS[name]!.size}`);
    }
    expect(app.icons?.some((i) => i.purpose === "maskable")).toBe(true);
  });
});
