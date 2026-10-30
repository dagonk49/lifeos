"use client";

import { useEffect } from "react";

import { listenForInstallPrompt, registerServiceWorker } from "@/lib/pwa";

/**
 * Registers the service worker of /sw.js on every page (ADR 0022: push, share target,
 * offline information page, nothing stored) and keeps the browser's install prompt for the
 * settings. Renders nothing.
 */
export function ServiceWorkerRegistration() {
  useEffect(() => {
    listenForInstallPrompt();
    void registerServiceWorker();
  }, []);
  return null;
}
