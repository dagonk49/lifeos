import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "LifeOS",
  description: "Assistant personnel et coach, local-first.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Every page is rendered per request, never prerendered at build time: Next.js puts the
  // nonce of the request's Content-Security-Policy (set by src/proxy.ts) on its scripts, and
  // a page built in advance could not carry it (the browser would block its scripts).
  await connection();
  return (
    <html lang="fr">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
