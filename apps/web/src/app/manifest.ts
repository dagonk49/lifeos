import type { MetadataRoute } from "next";

import { SHARE_ACCEPT } from "@/lib/share";

/**
 * Web app manifest (/manifest.webmanifest): LifeOS installed on a phone's home screen.
 * iOS delivers Web Push only to such an installed app (iOS 16.4 and later), ADR 0019; the
 * installed app is the mobile client (ADR 0022).
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "LifeOS",
    short_name: "LifeOS",
    description: "Assistant personnel et coach, local-first.",
    lang: "fr",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#fafaf9",
    theme_color: "#0f766e",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    // Long press on the icon (Android): straight to a quick capture.
    shortcuts: [
      { name: "Nouvelle tâche", short_name: "Tâche", url: "/tasks" },
      { name: "Parler à l'assistant", short_name: "Assistant", url: "/chat" },
      { name: "Envoyer un document", short_name: "Document", url: "/documents" },
    ],
    // « Partager → LifeOS » from the gallery or a file (Android, ADR 0022): a receipt photo,
    // a PDF. The service worker keeps the files for the documents page, where the owner
    // chooses the type and the space and sends them; nothing is uploaded before.
    share_target: {
      action: "/partage",
      method: "POST",
      enctype: "multipart/form-data",
      params: { files: [{ name: "file", accept: SHARE_ACCEPT }] },
    },
  };
}
