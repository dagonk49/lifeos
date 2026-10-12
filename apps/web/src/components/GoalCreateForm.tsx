"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { GoalFormFields } from "@/components/GoalFormFields";
import { Alert, Button, Card, Field, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { buildGoalCreate, EMPTY_GOAL_FORM, goalKeys, type GoalFormValues, type GoalKind } from "@/lib/goals";
import { useSpace } from "@/lib/space";
import type { GoalDetail } from "@/lib/types";

export function GoalCreateForm({
  onCreated,
  onCancel,
}: {
  onCreated: (goal: GoalDetail) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const { spaces, writeSpaceId } = useSpace();
  const [form, setForm] = useState<GoalFormValues>(EMPTY_GOAL_FORM);
  // "" = follow the space selected in the header (or the personal space).
  const [spaceId, setSpaceId] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const targetSpace = spaceId || writeSpaceId;

  const create = useMutation({
    mutationFn: (body: object) => api<GoalDetail>("/v1/goals", { method: "POST", idempotent: true, body }),
    onSuccess: (goal) => {
      queryClient.setQueryData(goalKeys.detail(goal.id), goal);
      void queryClient.invalidateQueries({ queryKey: goalKeys.all });
      setForm(EMPTY_GOAL_FORM);
      onCreated(goal);
    },
  });

  const update = (patch: Partial<GoalFormValues>) => {
    setInvalid(null);
    setForm((f) => ({ ...f, ...patch }));
  };

  return (
    <Card title="Nouvel objectif" className="mb-4">
      <form
        className="grid gap-3 sm:grid-cols-2"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const result = buildGoalCreate(form, targetSpace);
          if (!result.ok) {
            setInvalid(result.error);
            return;
          }
          create.mutate(result.value);
        }}
      >
        <Field label="Espace">
          <Select value={targetSpace} onChange={(e) => setSpaceId(e.target.value)}>
            {spaces.map((space) => (
              <option key={space.id} value={space.id}>
                {space.name}
              </option>
            ))}
          </Select>
        </Field>
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1 font-medium text-stone-700 dark:text-stone-300">Type d&apos;objectif</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1 py-1.5">
            {(
              [
                ["qualitative", "Qualitatif (jalons)"],
                ["measurable", "Mesurable (valeur cible)"],
              ] as [GoalKind, string][]
            ).map(([kind, label]) => (
              <label key={kind} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="goal-kind"
                  className="accent-teal-700"
                  checked={form.kind === kind}
                  onChange={() => update({ kind })}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <GoalFormFields form={form} onChange={update} />
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <Button type="submit" disabled={create.isPending || !form.title.trim()}>
            {create.isPending ? "Création…" : "Créer l'objectif"}
          </Button>
          <Button variant="secondary" onClick={onCancel}>
            Annuler
          </Button>
        </div>
        {(invalid || create.error) && (
          <div className="sm:col-span-2">
            <Alert tone="error">{invalid ?? errorMessage(create.error)}</Alert>
          </div>
        )}
      </form>
    </Card>
  );
}
