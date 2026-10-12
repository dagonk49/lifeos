"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Empty, Field, Input, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { buildCheckin, formatDecimal, goalKeys, type CheckinBody } from "@/lib/goals";
import type { Checkin, GoalDetail } from "@/lib/types";

export function GoalCheckins({ goal }: { goal: GoalDetail }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const measurable = goal.kind === "measurable";

  const add = useMutation({
    mutationFn: (body: CheckinBody) =>
      api<Checkin>(`/v1/goals/${goal.id}/checkins`, { method: "POST", body }),
    onSuccess: () => {
      setValue("");
      setNote("");
      void queryClient.invalidateQueries({ queryKey: goalKeys.all });
    },
  });

  return (
    <section aria-labelledby="goal-checkins">
      <h3 id="goal-checkins" className="text-sm font-semibold">
        Points d&apos;étape
      </h3>
      <form
        className="mt-2 flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const result = buildCheckin(goal.kind, value, note);
          if (!result.ok) {
            setInvalid(result.error);
            return;
          }
          add.mutate(result.value);
        }}
      >
        {measurable && (
          <Field
            label={goal.metric_unit ? `Valeur actuelle (${goal.metric_unit})` : "Valeur actuelle"}
            hint={goal.current_value ? `Dernière valeur : ${formatDecimal(goal.current_value, goal.metric_unit)}` : undefined}
          >
            <Input
              inputMode="decimal"
              autoComplete="off"
              value={value}
              onChange={(e) => {
                setInvalid(null);
                setValue(e.target.value);
              }}
            />
          </Field>
        )}
        <Field label={measurable ? "Note (facultatif si une valeur est indiquée)" : "Note"}>
          <Textarea
            className="min-h-16"
            maxLength={2000}
            value={note}
            onChange={(e) => {
              setInvalid(null);
              setNote(e.target.value);
            }}
          />
        </Field>
        <div>
          <Button type="submit" variant="secondary" disabled={add.isPending || (!value.trim() && !note.trim())}>
            Enregistrer le point
          </Button>
        </div>
        {(invalid || add.error) && <Alert tone="error">{invalid ?? errorMessage(add.error)}</Alert>}
      </form>

      <h4 className="mt-4 text-xs font-semibold tracking-wide text-stone-500 uppercase">Historique</h4>
      {goal.checkins.length === 0 ? (
        <Empty>Aucun point d&apos;étape pour l&apos;instant.</Empty>
      ) : (
        <ol className="mt-1 flex flex-col gap-2 border-l-2 border-stone-200 pl-3 text-sm dark:border-stone-700">
          {goal.checkins.map((c) => (
            <li key={c.id}>
              <p className="text-xs text-stone-500">
                <time dateTime={c.created_at}>{formatDateTime(c.created_at)}</time>
              </p>
              {c.value !== null && (
                <p className="font-medium tabular-nums">{formatDecimal(c.value, goal.metric_unit)}</p>
              )}
              {c.note && <p className="whitespace-pre-wrap text-stone-700 dark:text-stone-300">{c.note}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
