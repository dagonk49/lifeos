import { useSyncExternalStore } from "react";

import { isIos, isStandalone, SERVICE_WORKER_URL } from "@/lib/push";

/**
 * LifeOS installed on the phone (ADR 0022): the service worker of /sw.js on every page
 * (push, share target, the offline information page; it stores nothing), and the
 * browser's install prompt, offered from the settings.
 */

interface PwaEnvironment {
  navigator: Navigator;
  window: Window;
}

function environment(): PwaEnvironment | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return null;
  return { navigator, window };
}

/** Service workers exist only in a secure context (HTTPS, or localhost). */
export function canRegisterServiceWorker(env: PwaEnvironment | null = environment()): boolean {
  return env !== null && env.window.isSecureContext === true && "serviceWorker" in env.navigator;
}

/** Registers /sw.js for the whole site; false when unsupported or refused. */
export async function registerServiceWorker(env: PwaEnvironment | null = environment()): Promise<boolean> {
  if (!env || !canRegisterServiceWorker(env)) return false;
  try {
    await env.navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" });
    return true;
  } catch {
    return false;
  }
}

/** Chrome and Edge's `beforeinstallprompt` (not in the DOM typings). */
export interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: InstallPromptEvent | null = null;
let installed = false;
let listening = false;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

/**
 * Keeps the browser's install prompt for the « Installer LifeOS » button instead of the
 * banner it would show on its own. Called once, as early as possible (the event fires once).
 */
export function listenForInstallPrompt(target: Window | undefined = typeof window === "undefined" ? undefined : window): void {
  if (!target || listening) return;
  listening = true;
  target.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferred = event as InstallPromptEvent;
    notify();
  });
  target.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    notify();
  });
}

export interface InstallState {
  prompt: InstallPromptEvent | null;
  installed: boolean;
}

let snapshot: InstallState = { prompt: null, installed: false };

function currentState(): InstallState {
  if (snapshot.prompt !== deferred || snapshot.installed !== installed) {
    snapshot = { prompt: deferred, installed };
  }
  return snapshot;
}

const SERVER_STATE: InstallState = { prompt: null, installed: false };

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useInstallPrompt(): InstallState {
  return useSyncExternalStore(subscribe, currentState, () => SERVER_STATE);
}

/** Shows the browser's install dialog; it can be shown only once. */
export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const event = deferred;
  if (!event) return "unavailable";
  deferred = null;
  notify();
  await event.prompt();
  const { outcome } = await event.userChoice;
  return outcome;
}

/** How to install LifeOS from this browser. */
export type InstallMethod = "installed" | "prompt" | "ios" | "menu";

export function installMethod(
  state: InstallState,
  env: PwaEnvironment | null = environment(),
): InstallMethod {
  if (state.installed || isStandalone(env)) return "installed";
  if (state.prompt) return "prompt";
  if (env && isIos(env.navigator.userAgent, env.navigator.maxTouchPoints)) return "ios";
  return "menu";
}

/** Test hook: forgets the captured prompt and listeners. */
export function resetInstallPromptForTests(): void {
  deferred = null;
  installed = false;
  listening = false;
  listeners.clear();
  snapshot = { prompt: null, installed: false };
}
