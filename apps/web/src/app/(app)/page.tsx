"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { DashboardCard } from "@/components/dashboard/DashboardCards";
import { DashboardCustomize, saveLayout } from "@/components/dashboard/DashboardCustomize";
import { WeekView } from "@/components/dashboard/WeekView";
import { Alert, Button, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { DEFAULT_LAYOUT, mergeLayout } from "@/lib/dashboard";
import { formatDate } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { Dashboard, DashboardLayout } from "@/lib/types";

type View = DashboardLayout["default_view"];

export default function DashboardPage() {
  const { selected, writeSpaceId } = useSpace();
  const queryClient = useQueryClient();
  const [customizing, setCustomizing] = useState(false);
  // Chosen here before the saved view comes back from the server.
  const [view, setView] = useState<View | null>(null);
  const dashboard = useQuery({
    queryKey: ["dashboard", selected],
    queryFn: () => api<Dashboard>(`/v1/dashboard${spaceParam(selected)}`),
    refetchInterval: 120_000,
  });
  const persistView = useMutation({
    mutationFn: (next: { layout: DashboardLayout; view: View }) =>
      saveLayout({ ...next.layout, default_view: next.view }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
  });

  if (dashboard.isPending) return <Spinner />;
  if (dashboard.error) return <Alert tone="error">{errorMessage(dashboard.error)}</Alert>;
  const d = dashboard.data;
  const layout: DashboardLayout = d.layout
    ? { ...d.layout, cards: mergeLayout(d.layout.cards) }
    : DEFAULT_LAYOUT;
  const current: View = view ?? layout.default_view;
  const choose = (next: View) => {
    setView(next);
    if (next !== layout.default_view) persistView.mutate({ layout, view: next });
  };
  const visible = layout.cards.filter((c) => c.visible);

  return (
    <>
      <PageHeader title={`Aujourd'hui, ${formatDate(d.today)}`} documentTitle="Accueil">
        <div role="group" aria-label="Vue de l'accueil" className="flex gap-1">
          <Button size="sm" variant={current === "day" ? "primary" : "secondary"} aria-pressed={current === "day"} onClick={() => choose("day")}>
            Jour
          </Button>
          <Button
            size="sm"
            variant={current === "week" ? "primary" : "secondary"}
            aria-pressed={current === "week"}
            onClick={() => choose("week")}
          >
            Semaine
          </Button>
        </div>
        {current === "day" && !customizing && (
          <Button size="sm" variant="secondary" onClick={() => setCustomizing(true)}>
            Personnaliser
          </Button>
        )}
      </PageHeader>
      {persistView.error && <Alert tone="warning">{errorMessage(persistView.error)}</Alert>}
      {current === "week" && d.week ? (
        <WeekView key={`${d.week.start}-${selected ?? "all"}`} initial={d.week} spaceId={selected} today={d.today} />
      ) : (
        <>
          {customizing && (
            <div className="mb-4">
              <DashboardCustomize layout={layout} onDone={() => setCustomizing(false)} />
            </div>
          )}
          {visible.length === 0 ? (
            <Alert tone="info">Toutes les cartes sont masquées : « Personnaliser » permet d&apos;en afficher.</Alert>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {visible.map((card) => (
                <DashboardCard
                  key={card.id}
                  id={card.id}
                  d={d}
                  conflictSpace={selected ?? writeSpaceId}
                  onShowWeek={() => choose("week")}
                />
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
