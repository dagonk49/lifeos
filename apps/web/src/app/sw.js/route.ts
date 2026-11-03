import { SERVICE_WORKER_SOURCE } from "@/lib/serviceWorker";

// Served by the Next.js server itself (the image ships only .next/standalone and
// .next/static, no public/ directory). Never cached: browsers check it for updates.
export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response(SERVICE_WORKER_SOURCE, {
    headers: {
      "Content-Type": "text/javascript; charset=utf-8",
      "Cache-Control": "no-cache",
    },
  });
}
