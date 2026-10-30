"use client";

import { Field, Input, Select, Textarea } from "@/components/ui";
import { CADENCE_LABELS, type GoalFormValues, type ReviewCadence } from "@/lib/goals";

/** Fields shared by the creation and edit forms (the kind is chosen at creation only). */
export function GoalFormFields({
  form,
  onChange,
}: {
  form: GoalFormValues;
  onChange: (patch: Partial<GoalFormValues>) => void;
}) {
  const text =
    (key: keyof GoalFormValues) =>
    (e: { target: { value: string } }) =>
      onChange({ [key]: e.target.value });
  const measurable = form.kind === "measurable";
  return (
    <>
      <Field label="Intitulé" className="sm:col-span-2">
        <Input value={form.title} onChange={text("title")} required maxLength={200} autoComplete="off" />
      </Field>
      {measurable && (
        <>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              className="accent-teal-700"
              checked={form.savings}
              onChange={(e) => onChange({ savings: e.target.checked })}
            />
            Objectif d&apos;épargne (en euros, suivi dans la projection des finances)
          </label>
          {form.savings ? (
            <Field
              label="Versement mensuel prévu (facultatif)"
              hint="Sans versement déclaré, la date est estimée à partir du solde prévu de vos finances."
            >
              <Input
                inputMode="decimal"
                value={form.monthly_contribution}
                onChange={text("monthly_contribution")}
                autoComplete="off"
              />
            </Field>
          ) : (
            <Field label="Unité" hint="Ex. kg, km, pages">
              <Input value={form.metric_unit} onChange={text("metric_unit")} maxLength={30} autoComplete="off" />
            </Field>
          )}
          <Field
            label={form.savings ? "Déjà épargné" : "Valeur de départ"}
            hint={form.savings ? "En euros, au centime près (0 si vous partez de zéro)." : "Où vous en êtes aujourd'hui (0 si vous partez de zéro)."}
          >
            <Input
              inputMode="decimal"
              value={form.start_value}
              onChange={text("start_value")}
              required
              autoComplete="off"
            />
          </Field>
          <Field label={form.savings ? "Montant visé" : "Valeur cible"}>
            <Input
              inputMode="decimal"
              value={form.target_value}
              onChange={text("target_value")}
              required
              autoComplete="off"
            />
          </Field>
        </>
      )}
      <Field label="Début">
        <Input type="date" value={form.start_date} onChange={text("start_date")} />
      </Field>
      <Field label="Échéance">
        <Input type="date" value={form.due_date} min={form.start_date || undefined} onChange={text("due_date")} />
      </Field>
      <Field label="Fréquence de suivi" hint="À quel rythme faire le point sur cet objectif.">
        <Select
          value={form.review_cadence}
          onChange={(e) => onChange({ review_cadence: e.target.value as ReviewCadence })}
        >
          {(Object.keys(CADENCE_LABELS) as ReviewCadence[]).map((cadence) => (
            <option key={cadence} value={cadence}>
              {CADENCE_LABELS[cadence]}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Pourquoi c'est important" className="sm:col-span-2">
        <Textarea value={form.motivation} onChange={text("motivation")} maxLength={2000} className="min-h-20" />
      </Field>
      <Field label="Description" className="sm:col-span-2">
        <Textarea value={form.description} onChange={text("description")} maxLength={5000} className="min-h-20" />
      </Field>
    </>
  );
}
