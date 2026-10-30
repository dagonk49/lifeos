"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { TransactionForm } from "@/components/TransactionForm";
import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Spinner } from "@/components/ui";
import { api, isApiError, newIdempotencyKey } from "@/lib/api";
import {
  BASIS_LABELS,
  BASIS_TONES,
  CADENCE_LABELS,
  categoriesPath,
  categoryChoices,
  decimalToInput,
  DIRECTION_LABELS,
  emptyRecurringForm,
  emptyTransactionForm,
  financeErrorMessage,
  financeKeys,
  idempotencyKeyFor,
  RECURRING_CHANGE_KEYS,
  recurringAmountText,
  recurringPath,
  recurringToForm,
  validateRecurring,
  type Direction,
  type PendingKey,
  type RecurringFormValues,
} from "@/lib/finance";
import { formatDate } from "@/lib/format";
import type { Cadence, FinanceCategory, RecurringItem } from "@/lib/types";

const CADENCES = Object.keys(CADENCE_LABELS) as Cadence[];

function RecurringForm({
  spaceId,
  item,
  today,
  onSaved,
  onCancel,
}: {
  spaceId: string;
  item?: RecurringItem;
  today: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<RecurringFormValues>(() => (item ? recurringToForm(item) : emptyRecurringForm(today)));
  const [invalid, setInvalid] = useState<string | null>(null);
  const pending = useRef<PendingKey | null>(null);
  const categories = useQuery({
    queryKey: financeKeys.categories(spaceId, false),
    queryFn: () => api<FinanceCategory[]>(categoriesPath(spaceId, false)),
  });
  const choices = categoryChoices(categories.data ?? [], spaceId, form.direction, form.category_id || null);
  const set = (patch: Partial<RecurringFormValues>) => {
    setInvalid(null);
    setForm((f) => ({ ...f, ...patch }));
  };
  const refresh = () => {
    for (const queryKey of RECURRING_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
  };
  const save = useMutation({
    mutationFn: (body: object) =>
      item
        ? api<RecurringItem>(`/v1/finance/recurring/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            body,
            headers: { "If-Match": String(item.version) },
          })
        : api<RecurringItem>("/v1/finance/recurring", {
            method: "POST",
            body,
            headers: { "Idempotency-Key": (pending.current = idempotencyKeyFor(pending.current, body, newIdempotencyKey)).key },
          }),
    onSuccess: () => {
      pending.current = null;
      refresh();
      onSaved();
    },
    onError: (error) => {
      if (isApiError(error, "version_conflict")) refresh();
    },
  });

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const result = validateRecurring(form);
        if (!result.ok) return setInvalid(result.error);
        save.mutate(item ? result.value : { space_id: spaceId, ...result.value });
      }}
    >
      <Field label="Nom de l'échéance" hint="Ex. Loyer, Salaire, Électricité, Achat d'un vélo">
        <Input value={form.label} maxLength={120} onChange={(e) => set({ label: e.target.value })} autoComplete="off" />
      </Field>
      <Field label="Sens">
        <Select
          value={form.direction}
          onChange={(e) => set({ direction: e.target.value as Direction, category_id: "" })}
        >
          {(Object.keys(DIRECTION_LABELS) as Direction[]).map((d) => (
            <option key={d} value={d}>
              {DIRECTION_LABELS[d]}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Montant habituel" hint="Exact, au centime près (ex. 750 ou 64,90).">
        <Input inputMode="decimal" value={form.amount} onChange={(e) => set({ amount: e.target.value })} autoComplete="off" />
      </Field>
      <Field label="Fréquence">
        <Select value={form.cadence} onChange={(e) => set({ cadence: e.target.value as Cadence })}>
          {CADENCES.map((c) => (
            <option key={c} value={c}>
              {CADENCE_LABELS[c]}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={form.cadence === "once" ? "Date" : "Première échéance"}>
        <Input type="date" value={form.start_date} onChange={(e) => set({ start_date: e.target.value })} />
      </Field>
      {form.cadence !== "once" && (
        <Field label="Fin (facultatif)">
          <Input type="date" value={form.end_date} min={form.start_date || undefined} onChange={(e) => set({ end_date: e.target.value })} />
        </Field>
      )}
      <Field label="Catégorie (facultatif)">
        <Select value={form.category_id} onChange={(e) => set({ category_id: e.target.value })}>
          <option value="">Aucune</option>
          {choices.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
      </Field>
      <label className="flex items-center gap-2 self-end text-sm">
        <input type="checkbox" className="accent-teal-700" checked={form.variable} onChange={(e) => set({ variable: e.target.checked })} />
        Montant variable (estimé)
      </label>
      {form.variable && (
        <>
          <Field label="Au plus bas (facultatif)">
            <Input inputMode="decimal" value={form.low} onChange={(e) => set({ low: e.target.value })} autoComplete="off" />
          </Field>
          <Field label="Au plus haut (facultatif)">
            <Input inputMode="decimal" value={form.high} onChange={(e) => set({ high: e.target.value })} autoComplete="off" />
          </Field>
        </>
      )}
      <div className="flex flex-wrap gap-2 sm:col-span-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Enregistrement…" : item ? "Enregistrer les modifications" : "Ajouter l'échéance"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Annuler
        </Button>
      </div>
      {(invalid || save.error) && (
        <div className="sm:col-span-2">
          <Alert tone="error">{invalid ?? financeErrorMessage(save.error)}</Alert>
        </div>
      )}
    </form>
  );
}

function RecurringRow({ item, spaceId, today }: { item: RecurringItem; spaceId: string; today: string }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"view" | "edit" | "record" | "delete">("view");
  const remove = useMutation({
    mutationFn: () => api(`/v1/finance/recurring/${encodeURIComponent(item.id)}`, { method: "DELETE" }),
    onSuccess: () => {
      for (const queryKey of RECURRING_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
    },
  });
  const basis = item.amount_is_estimate ? "estimated" : "scheduled";
  return (
    <li className="flex flex-col gap-2 border-b border-stone-100 py-2 text-sm last:border-0 dark:border-stone-800">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium break-words">{item.label}</span>
        <Badge tone={BASIS_TONES[basis]}>{BASIS_LABELS[basis]}</Badge>
        <span className="text-muted">
          {DIRECTION_LABELS[item.direction]} · {CADENCE_LABELS[item.cadence]}
          {item.next_date ? ` · prochaine le ${formatDate(item.next_date)}` : " · terminée"}
        </span>
        <span className="ml-auto tabular-nums">{recurringAmountText(item)}</span>
      </div>
      {mode === "view" && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => setMode("record")}>
            Enregistrer cette échéance
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("edit")}>
            Modifier
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("delete")}>
            Supprimer
          </Button>
        </div>
      )}
      {mode === "edit" && (
        <RecurringForm spaceId={spaceId} item={item} today={today} onSaved={() => setMode("view")} onCancel={() => setMode("view")} />
      )}
      {mode === "record" && (
        <TransactionForm
          spaceId={spaceId}
          today={today}
          recurringId={item.id}
          initial={{
            ...emptyTransactionForm(today),
            amount: decimalToInput(item.amount),
            direction: item.direction,
            currency: item.currency,
            merchant: item.merchant ?? "",
            label: item.label,
            category_id: item.category_id ?? "",
          }}
          submitLabel="Enregistrer l'opération"
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: financeKeys.recurringAll });
            setMode("view");
          }}
          onCancel={() => setMode("view")}
        />
      )}
      {mode === "delete" && (
        <div role="group" aria-label={`Supprimer l'échéance « ${item.label} » ?`} className="flex flex-col gap-2 rounded-md border border-stone-300 p-3 dark:border-stone-700">
          <p className="font-medium">Supprimer l&apos;échéance « {item.label} » ?</p>
          <p className="text-stone-600 dark:text-stone-400">Les opérations déjà enregistrées restent, sans lien avec elle.</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
              Supprimer « {item.label} »
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setMode("view")}>
              Garder
            </Button>
          </div>
          {remove.error && <Alert tone="error">{financeErrorMessage(remove.error)}</Alert>}
        </div>
      )}
    </li>
  );
}

/** Task 26: recurring expenses and incomes (or a planned purchase), declared by the owner. */
export function RecurringList({ spaceId, today }: { spaceId: string; today: string }) {
  const [adding, setAdding] = useState(false);
  const list = useQuery({
    queryKey: financeKeys.recurring(spaceId),
    queryFn: () => api<RecurringItem[]>(recurringPath(spaceId)),
  });
  return (
    <Card
      title="Échéances récurrentes"
      actions={
        !adding && (
          <Button size="sm" variant="secondary" onClick={() => setAdding(true)}>
            Nouvelle échéance
          </Button>
        )
      }
    >
      {adding && (
        <div className="mb-3">
          <RecurringForm spaceId={spaceId} today={today} onSaved={() => setAdding(false)} onCancel={() => setAdding(false)} />
        </div>
      )}
      {list.isPending ? (
        <Spinner />
      ) : list.error ? (
        <Alert tone="error">{financeErrorMessage(list.error)}</Alert>
      ) : (list.data ?? []).length === 0 ? (
        !adding && (
          <Empty>
            Déclarez vos dépenses et revenus réguliers (loyer, salaire, abonnements) : ils deviennent « prévus » dans la
            projection.
          </Empty>
        )
      ) : (
        <ul aria-label="Échéances">
          {(list.data ?? []).map((item) => (
            <RecurringRow key={item.id} item={item} spaceId={spaceId} today={today} />
          ))}
        </ul>
      )}
    </Card>
  );
}
