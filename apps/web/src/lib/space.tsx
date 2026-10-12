"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { Space } from "./types";

const STORAGE_KEY = "lifeos.space";

interface SpaceState {
  spaces: Space[];
  /** null = all spaces (read views only). */
  selected: string | null;
  setSelected: (id: string | null) => void;
  /** Space used for creations: the selected one, else the personal space. */
  writeSpaceId: string;
  spaceName: (id: string) => string;
}

const SpaceContext = createContext<SpaceState | null>(null);

export function SpaceProvider({ spaces, children }: { spaces: Space[]; children: ReactNode }) {
  const [selected, setSelectedState] = useState<string | null>(null);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored && spaces.some((s) => s.id === stored)) setSelectedState(stored);
    } catch {
      // Storage unavailable (private mode): keep the default.
    }
  }, [spaces]);

  const value = useMemo<SpaceState>(() => {
    const personal = spaces.find((s) => s.kind === "personal") ?? spaces[0];
    return {
      spaces,
      selected,
      setSelected: (id) => {
        setSelectedState(id);
        try {
          if (id) window.localStorage.setItem(STORAGE_KEY, id);
          else window.localStorage.removeItem(STORAGE_KEY);
        } catch {
          // ignore
        }
      },
      writeSpaceId: selected ?? personal?.id ?? "",
      spaceName: (id) => spaces.find((s) => s.id === id)?.name ?? "",
    };
  }, [spaces, selected]);

  return <SpaceContext.Provider value={value}>{children}</SpaceContext.Provider>;
}

export function useSpace(): SpaceState {
  const value = useContext(SpaceContext);
  if (!value) throw new Error("useSpace must be used inside SpaceProvider");
  return value;
}

/** Query string fragment for list endpoints. */
export function spaceParam(selected: string | null, prefix = "?"): string {
  return selected ? `${prefix}space_id=${encodeURIComponent(selected)}` : "";
}
