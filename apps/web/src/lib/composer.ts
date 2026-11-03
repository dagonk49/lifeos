"use client";

import { useEffect, useState } from "react";

/**
 * Whether Enter sends the message. With a mouse and keyboard, Enter sends and Shift+Enter
 * goes to the next line. On a touch screen (coarse pointer) the virtual keyboard's Enter
 * must be able to type a new line, so only the « Envoyer » button sends. Never while an
 * input method is composing a word (Enter then validates the word).
 */
export function enterSends(
  event: { key: string; shiftKey: boolean; isComposing?: boolean },
  coarsePointer: boolean,
): boolean {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing && !coarsePointer;
}

export const COMPOSER_HINTS = {
  fine: "Entrée envoie le message, Maj+Entrée va à la ligne.",
  coarse: "Entrée va à la ligne ; le bouton « Envoyer » envoie le message.",
} as const;

const COARSE = "(pointer: coarse)";

/** True on a touch screen without a precise pointer (phone, tablet). */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(COARSE);
    setCoarse(query.matches);
    const onChange = (event: MediaQueryListEvent) => setCoarse(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return coarse;
}
