"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Alert, Button, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { DerivedItem } from "@/lib/types";

const DERIVED_LABELS: Record<DerivedItem["type"], string> = {
  fact: "Fait",
  task: "Tâche",
  memory: "Souvenir",
};

/**
 * Named confirmation of an erasure (docs/politique-de-donnees.md §2). With `derivedPath`, it
 * first lists what was saved from the object (facts, tasks, memories): these stay unless the
 * owner ticks the box, which sends `with_derived=true`.
 */
export function EraseConfirm({
  question,
  note,
  confirmLabel,
  deletePath,
  derivedPath,
  onDone,
  onCancel,
}: {
  question: string;
  note?: string;
  confirmLabel: string;
  deletePath: string;
  derivedPath?: string;
  onDone: (withDerived: boolean) => void;
  onCancel: () => void;
}) {
  const checkboxId = useId();
  const [withDerived, setWithDerived] = useState(false);
  const derived = useQuery({
    queryKey: ["derived", derivedPath],
    queryFn: () => api<{ items: DerivedItem[] }>(derivedPath ?? ""),
    enabled: derivedPath !== undefined,
    staleTime: 0,
    gcTime: 0,
  });
  const items = derived.data?.items ?? [];
  const erase = useMutation({
    mutationFn: (both: boolean) =>
      api(`${deletePath}${both ? "?with_derived=true" : ""}`, { method: "DELETE" }),
    onSuccess: (_, both) => onDone(both),
  });
  const waiting = derivedPath !== undefined && derived.isPending;

  return (
    <div
      role="group"
      aria-label={question}
      className="mt-3 flex flex-col gap-2 rounded-md border border-stone-300 p-3 text-sm dark:border-stone-700"
    >
      <p className="font-medium">{question}</p>
      {note && <p className="text-stone-600 dark:text-stone-400">{note}</p>}
      {waiting && <Spinner label="Recherche de ce qui en a été tiré…" />}
      {derived.error && <Alert tone="warning">{errorMessage(derived.error)}</Alert>}
      {derivedPath !== undefined && derived.isSuccess && items.length === 0 && (
        <p className="text-stone-600 dark:text-stone-400">
          Aucun fait, aucune tâche ni aucun souvenir n&apos;en a été tiré.
        </p>
      )}
      {items.length > 0 && (
        <>
          <p className="text-stone-600 dark:text-stone-400">
            Ce qui en a été tiré reste enregistré, sauf si vous le supprimez aussi :
          </p>
          <ul aria-label="Ce qui en a été tiré" className="flex flex-col gap-1">
            {items.map((item) => (
              <li key={`${item.type}-${item.id}`}>
                <span className="font-medium">{DERIVED_LABELS[item.type]}</span> : {item.text}
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-2">
            <input
              id={checkboxId}
              type="checkbox"
              checked={withDerived}
              onChange={(e) => setWithDerived(e.target.checked)}
            />
            <label htmlFor={checkboxId}>
              Supprimer aussi ce qui en a été tiré ({items.length})
            </label>
          </div>
        </>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="danger"
          onClick={() => erase.mutate(withDerived && items.length > 0)}
          disabled={erase.isPending || waiting}
        >
          {confirmLabel}
        </Button>
        <Button size="sm" variant="secondary" onClick={onCancel} disabled={erase.isPending}>
          Annuler
        </Button>
      </div>
      {erase.error && <Alert tone="error">{errorMessage(erase.error)}</Alert>}
    </div>
  );
}
