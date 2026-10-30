import { SHARE_PAGE_FAILED } from "@/lib/share";

/**
 * Share target of the installed app (manifest `share_target`, ADR 0022). The service worker
 * answers POST /partage itself and keeps the files in memory for the documents page. This
 * route only runs when no worker is active yet (first visit, cleared site data): the files
 * are dropped here, never stored nor forwarded, and the page asks to pick them again.
 */
export const dynamic = "force-dynamic";

function redirect(location: string): Response {
  // A relative Location: the public origin is the one the browser used.
  return new Response(null, { status: 303, headers: { Location: location, "Cache-Control": "no-store" } });
}

export function POST(): Response {
  return redirect(SHARE_PAGE_FAILED);
}

export function GET(): Response {
  return redirect("/documents");
}
