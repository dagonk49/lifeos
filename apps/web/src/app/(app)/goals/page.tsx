"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { GoalCard } from "@/components/GoalCard";
import { GoalCreateForm } from "@/components/GoalCreateForm";
import { GoalDetailPanel } from "@/components/GoalDetail";
import { Alert, Button, Empty, PageHeader, Select, Spinner, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { todayLocal } from "@/lib/format";
import { goalKeys, goalsPath, STATUS_FILTERS, type GoalStatusFilter } from "@/lib/goals";
import { useSpace } from "@/lib/space";
import type { GoalSummary } from "@/lib/types";

function GoalsView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { selected, spaceName } = useSpace();
  const [filter, setFilter] = useState<GoalStatusFilter>("active");
  const [creating, setCreating] = useState(false);
  const selectedId = searchParams.get("id");
  const today = todayLocal();

  const goals = useQuery({
    queryKey: goalKeys.list(selected, filter),
    queryFn: () => api<GoalSummary[]>(goalsPath(selected, filter)),
  });
  const filterInfo = STATUS_FILTERS.find((f) => f.value === filter) ?? STATUS_FILTERS[0];

  const openGoal = (id: string) => router.push(`${pathname}?id=${encodeURIComponent(id)}`);
  const closeGoal = () => router.replace(pathname, { scroll: false });

  return (
    <>
      <PageHeader title="Objectifs">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-stone-600 dark:text-stone-400">Statut</span>
          <Select
            className="w-auto py-1"
            value={filter}
            onChange={(e) => setFilter(e.target.value as GoalStatusFilter)}
          >
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
        </label>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            Nouvel objectif
          </Button>
        )}
      </PageHeader>

      {creating && (
        <GoalCreateForm
          onCancel={() => setCreating(false)}
          onCreated={(goal) => {
            setCreating(false);
            openGoal(goal.id);
          }}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        {/* On a phone, the open goal replaces the list; "Fermer" brings it back. */}
        <section aria-labelledby="goals-list-title" className={cx(selectedId && "hidden lg:block")}>
          <h2 id="goals-list-title" className="sr-only">
            Liste des objectifs
          </h2>
          {goals.isPending ? (
            <Spinner />
          ) : goals.error ? (
            <Alert tone="error">{errorMessage(goals.error)}</Alert>
          ) : goals.data.length === 0 ? (
            <div className="rounded-lg border border-dashed border-stone-300 p-4 dark:border-stone-700">
              <Empty>{filterInfo?.empty}</Empty>
            </div>
          ) : (
            <ul className="flex flex-col gap-3">
              {goals.data.map((goal) => (
                <GoalCard
                  key={goal.id}
                  goal={goal}
                  current={goal.id === selectedId}
                  spaceLabel={selected ? undefined : spaceName(goal.space_id)}
                  today={today}
                />
              ))}
            </ul>
          )}
        </section>

        {selectedId ? (
          <GoalDetailPanel key={selectedId} goalId={selectedId} onClose={closeGoal} />
        ) : (
          <div className="hidden rounded-lg border border-dashed border-stone-300 p-6 text-center text-sm text-muted lg:block dark:border-stone-700">
            Sélectionnez un objectif pour voir sa progression, ses jalons et ses points d&apos;étape.
          </div>
        )}
      </div>
    </>
  );
}

export default function GoalsPage() {
  // useSearchParams needs a Suspense boundary for the static build.
  return (
    <Suspense fallback={<Spinner />}>
      <GoalsView />
    </Suspense>
  );
}
