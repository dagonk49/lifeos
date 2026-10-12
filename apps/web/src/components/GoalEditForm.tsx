"use client";

import { useState } from "react";

import { GoalFormFields } from "@/components/GoalFormFields";
import { Alert, Button } from "@/components/ui";
import { buildGoalUpdate, goalToForm, type GoalFields, type GoalFormValues } from "@/lib/goals";
import type { GoalDetail } from "@/lib/types";

/** Edits a goal; `onSave` receives only the changed fields. */
export function GoalEditForm({
  goal,
  saving,
  error,
  onSave,
  onCancel,
}: {
  goal: GoalDetail;
  saving: boolean;
  error: string | null;
  onSave: (body: Partial<GoalFields>) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<GoalFormValues>(() => goalToForm(goal));
  const [invalid, setInvalid] = useState<string | null>(null);

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      noValidate
      aria-label={`Modifier « ${goal.title} »`}
      onSubmit={(e) => {
        e.preventDefault();
        const result = buildGoalUpdate(goal, form);
        if (!result.ok) {
          setInvalid(result.error);
          return;
        }
        if (Object.keys(result.value).length === 0) {
          onCancel();
          return;
        }
        onSave(result.value);
      }}
    >
      <GoalFormFields
        form={form}
        onChange={(patch) => {
          setInvalid(null);
          setForm((f) => ({ ...f, ...patch }));
        }}
      />
      <div className="flex flex-wrap gap-2 sm:col-span-2">
        <Button type="submit" disabled={saving || !form.title.trim()}>
          {saving ? "Enregistrement…" : "Enregistrer"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Annuler
        </Button>
      </div>
      {(invalid || error) && (
        <div className="sm:col-span-2">
          <Alert tone="error">{invalid ?? error}</Alert>
        </div>
      )}
    </form>
  );
}
