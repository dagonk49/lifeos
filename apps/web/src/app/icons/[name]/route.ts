import { iconPng } from "@/lib/pwaIcon";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ name: string }> },
): Promise<Response> {
  const png = iconPng((await params).name);
  if (!png) return new Response("Not found", { status: 404 });
  return new Response(png as BodyInit, {
    headers: {
      "Content-Type": "image/png",
      // The same image for a day; a new design would change the file name.
      "Cache-Control": "public, max-age=86400",
    },
  });
}
