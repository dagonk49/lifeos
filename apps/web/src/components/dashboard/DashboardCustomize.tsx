"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Card } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { cardTitle, DEFAULT_LAYOUT, moveCard, toggleCard } from "@/lib/dashboard";
import type { DashboardLayout } from "@/lib/types";

export function saveLayout(layout: Pick<DashboardLayout, "cards" | "default_view" | "week_start">) {
  return api<DashboardLayout>("/v1/dashboard/layout", {
    method: "PUT",
    body: { cards: layout.cards, default_view: layout.default_view, week_start: layout.week_start },
  });
}

/** Show or hide each card and change its place with buttons (keyboard first, no drag). */
export function DashboardCustomize({ layout, onDone }: { layout: DashboardLayout; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [cards, setCards] = useState(layout.cards);
  const [weekStart, setWeekStart] = useState(layout.week_start);
  const save = useMutation({
    mutationFn: (next: Pick<DashboardLayout, "cards" | "week_start">) =>
      saveLayout({ ...next, default_view: layout.default_view }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      onDone();
    },
  });
  return (
    <Card title="Personnaliser l'accueil" footer="Enregistré sur votre serveur : la même disposition sur tous vos appareils.">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ cards, week_start: weekStart });
        }}
      >
        <ol className="flex flex-col gap-1" aria-label="Cartes de l'accueil">
          {cards.map((card, index) => {
            const title = cardTitle(card.id);
            return (
              <li key={card.id} className="flex flex-wrap items-center gap-2 rounded-md border border-stone-200 px-2 py-1 dark:border-stone-800">
                <label className="flex min-w-0 flex-1 items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4 accent-teal-700"
                    checked={card.visible}
                    onChange={(e) => setCards(toggleCard(cards, card.id, e.target.checked))}
                  />
                  Afficher « {title} »
                </label>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={index === 0}
                  aria-label={`Monter « ${title} »`}
                  onClick={() => setCards(moveCard(cards, index, -1))}
                >
                  Monter
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={index === cards.length - 1}
                  aria-label={`Descendre « ${title} »`}
                  onClick={() => setCards(moveCard(cards, index, 1))}
                >
                  Descendre
                </Button>
              </li>
            );
          })}
        </ol>
        <fieldset className="flex flex-wrap gap-4 text-sm">
          <legend className="mb-1 font-medium">La semaine commence</legend>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="week-start"
              className="accent-teal-700"
              checked={weekStart === "monday"}
              onChange={() => setWeekStart("monday")}
            />
            le lundi
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="week-start"
              className="accent-teal-700"
              checked={weekStart === "today"}
              onChange={() => setWeekStart("today")}
            />
            aujourd&apos;hui
          </label>
        </fieldset>
        {save.error && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            {errorMessage(save.error)}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={save.isPending}>
            Enregistrer la disposition
          </Button>
          <Button
            variant="secondary"
            disabled={save.isPending}
            onClick={() => {
              setCards(DEFAULT_LAYOUT.cards);
              setWeekStart(DEFAULT_LAYOUT.week_start);
            }}
          >
            Rétablir l&apos;ordre par défaut
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Annuler
          </Button>
        </div>
      </form>
    </Card>
  );
}
