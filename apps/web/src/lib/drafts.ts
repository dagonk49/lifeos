"use client";

import { useCallback, useEffect, useState } from "react";

function read(key: string): string {
  try {
    return window.sessionStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function write(key: string, value: string): void {
  try {
    if (value) window.sessionStorage.setItem(key, value);
    else window.sessionStorage.removeItem(key);
  } catch {
    // Storage disabled or full: the draft only lives in the page.
  }
}

/**
 * A text being written that survives a reload or a sign-in in this tab (sessionStorage:
 * never shared with other tabs, gone when the tab closes, cleared on sign-out). Emptying it,
 * once sent, forgets it. Read after mounting: the server renders an empty field.
 */
export function useSessionDraft(
  key: string,
): [string, (next: string | ((current: string) => string)) => void] {
  const [value, setValue] = useState("");
  useEffect(() => {
    const stored = read(key);
    if (stored) setValue(stored);
  }, [key]);
  const update = useCallback(
    (next: string | ((current: string) => string)) => {
      setValue((current) => {
        const value = typeof next === "function" ? next(current) : next;
        write(key, value);
        return value;
      });
    },
    [key],
  );
  return [value, update];
}
