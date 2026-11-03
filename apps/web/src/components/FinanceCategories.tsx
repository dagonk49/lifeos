"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import {
  buildCategoryCreate,
  buildCategoryUpdate,
  CATEGORY_CHANGE_KEYS,
  CATEGORY_NAME_MAX,
  categoriesPath,
  decimalToInput,
  EMPTY_CATEGORY_FORM,
  financeErrorMessage,
  financeKeys,
  formatMoney,
  KIND_LABELS,
  sortCategories,
  type CategoryFormValues,
  type CategoryKind,
  type CategoryUpdateBody,
} from "@/lib/finance";
import type { FinanceCategory } from "@/lib/types";

function CategoryRow({
  category,
  onPatch,
  onDelete,
  busy,
}: {
  category: FinanceCategory;
  onPatch: (body: CategoryUpdateBody, done?: () => void) => void;
  onDelete: () => void;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(category.name);
  const [budget, setBudget] = useState(decimalToInput(category.monthly_budget));
  const [invalid, setInvalid] = useState<string | null>(null);
  const expense = category.kind === "expense";

  if (editing) {
    return (
      <li className="rounded-md border border-teal-300 p-3 dark:border-teal-800">
        <form
          className="grid gap-2 sm:grid-cols-2"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            const result = buildCategoryUpdate(category, { name, budget: expense ? budget : "" });
            if (!result.ok) return setInvalid(result.error);
            if (Object.keys(result.value).length === 0) return setEditing(false);
            onPatch(result.value, () => setEditing(false));
          }}
        >
          <Field label="Nom">
            <Input value={name} maxLength={CATEGORY_NAME_MAX} onChange={(e) => setName(e.target.value)} required />
          </Field>
          {expense && (
            <Field label="Budget mensuel" hint="Vide : pas de budget.">
              <Input inputMode="decimal" autoComplete="off" value={budget} onChange={(e) => setBudget(e.target.value)} />
            </Field>
          )}
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <Button type="submit" size="sm" disabled={busy}>
              Enregistrer
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setEditing(false);
                setInvalid(null);
                setName(category.name);
                setBudget(decimalToInput(category.monthly_budget));
              }}
            >
              Annuler
            </Button>
          </div>
          {invalid && (
            <div className="sm:col-span-2">
              <Alert tone="error">{invalid}</Alert>
            </div>
          )}
        </form>
      </li>
    );
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-stone-200 px-3 py-2 dark:border-stone-800">
      <div className="min-w-0">
        <p className="font-medium break-words">
          {category.name} {category.archived && <Badge>archivée</Badge>}
        </p>
        {expense && (
          <p className="text-xs text-muted">
            {category.monthly_budget !== null
              ? `Budget : ${formatMoney(category.monthly_budget)} par mois`
              : "Sans budget"}
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {!category.archived && (
          <Button size="sm" variant="secondary" aria-label={`Modifier « ${category.name} »`} onClick={() => setEditing(true)}>
            Modifier
          </Button>
        )}
        {expense && !category.archived && category.monthly_budget !== null && (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            aria-label={`Retirer le budget de « ${category.name} »`}
            onClick={() => onPatch({ monthly_budget: null })}
          >
            Retirer le budget
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          aria-label={`${category.archived ? "Réactiver" : "Archiver"} « ${category.name} »`}
          onClick={() => onPatch({ archived: !category.archived })}
        >
          {category.archived ? "Réactiver" : "Archiver"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          aria-label={`Supprimer « ${category.name} »`}
          onClick={() => {
            if (
              window.confirm(
                `Supprimer la catégorie « ${category.name} » ? Ses opérations deviendront non classées et ce que LifeOS a appris pour elle sera oublié. Pour la garder dans l'historique, archivez-la plutôt.`,
              )
            ) {
              onDelete();
            }
          }}
        >
          Supprimer
        </Button>
      </div>
    </li>
  );
}

/** Categories of one space: budgets, creation, archiving, default French categories. */
export function FinanceCategories({ spaceId }: { spaceId: string }) {
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const [form, setForm] = useState<CategoryFormValues>(EMPTY_CATEGORY_FORM);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const categories = useQuery({
    queryKey: financeKeys.categories(spaceId, true),
    queryFn: () => api<FinanceCategory[]>(categoriesPath(spaceId, true)),
  });
  const refresh = () => {
    for (const queryKey of CATEGORY_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
  };

  const defaults = useMutation({
    mutationFn: () =>
      api<FinanceCategory[]>("/v1/finance/categories/defaults", { method: "POST", body: { space_id: spaceId } }),
    onSuccess: (created) => setNotice(`${created.length} catégories créées. Ajustez-les librement.`),
    onSettled: refresh,
  });
  const create = useMutation({
    mutationFn: (body: object) =>
      api<FinanceCategory>("/v1/finance/categories", { method: "POST", idempotent: true, body }),
    onSuccess: (created) => {
      setForm(EMPTY_CATEGORY_FORM);
      setNotice(`Catégorie « ${created.name} » ajoutée.`);
    },
    onSettled: refresh,
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: CategoryUpdateBody; done?: () => void }) =>
      api<FinanceCategory>(`/v1/finance/categories/${encodeURIComponent(id)}`, { method: "PATCH", body }),
    onSuccess: (updated, { body, done }) => {
      done?.();
      if (body.archived === true) setNotice(`« ${updated.name} » est archivée : elle n'est plus proposée.`);
      else if (body.archived === false) setNotice(`« ${updated.name} » est de nouveau proposée.`);
      else setNotice(`« ${updated.name} » est enregistrée.`);
    },
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: (category: FinanceCategory) =>
      api(`/v1/finance/categories/${encodeURIComponent(category.id)}`, { method: "DELETE" }),
    onSuccess: (_, category) => setNotice(`Catégorie « ${category.name} » supprimée.`),
    onSettled: refresh,
  });
  const busy = patch.isPending || remove.isPending;
  const mutationError = defaults.error ?? patch.error ?? remove.error;

  const all = sortCategories(categories.data ?? []);
  const archivedCount = all.filter((c) => c.archived).length;
  const visible = all.filter((c) => showArchived || !c.archived);
  const update = (change: Partial<CategoryFormValues>) => {
    setInvalid(null);
    setForm((current) => ({ ...current, ...change }));
  };

  return (
    <Card title="Catégories">
      {notice && (
        <div className="mb-3">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}
      {mutationError && (
        <div className="mb-3">
          <Alert tone="error">{financeErrorMessage(mutationError)}</Alert>
        </div>
      )}

      {categories.isPending ? (
        <Spinner />
      ) : !categories.data ? (
        <Alert tone="error">{financeErrorMessage(categories.error)}</Alert>
      ) : all.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-2">
          <Empty>Aucune catégorie dans cet espace pour l&apos;instant.</Empty>
          <Button onClick={() => defaults.mutate()} disabled={defaults.isPending}>
            {defaults.isPending ? "Création…" : "Créer les catégories par défaut"}
          </Button>
          <p className="text-center text-xs text-muted">
            Alimentation, Logement, Transports, Santé, Loisirs… et Salaire pour les revenus.
          </p>
        </div>
      ) : (
        <>
          {(["expense", "income"] as CategoryKind[]).map((kind) => {
            const group = visible.filter((c) => c.kind === kind);
            if (group.length === 0) return null;
            return (
              <div key={kind} className="mb-3">
                <h3 className="mb-1.5 text-sm font-semibold">{KIND_LABELS[kind]}</h3>
                <ul className="flex flex-col gap-1.5 text-sm">
                  {group.map((category) => (
                    <CategoryRow
                      key={`${category.id}-${category.updated_at}`}
                      category={category}
                      busy={busy}
                      onPatch={(body, done) => patch.mutate({ id: category.id, body, done })}
                      onDelete={() => remove.mutate(category)}
                    />
                  ))}
                </ul>
              </div>
            );
          })}
          {archivedCount > 0 && (
            <Button size="sm" variant="ghost" onClick={() => setShowArchived((v) => !v)}>
              {showArchived ? "Masquer les catégories archivées" : `Afficher les catégories archivées (${archivedCount})`}
            </Button>
          )}
        </>
      )}

      <form
        className="mt-4 grid gap-2 border-t border-stone-100 pt-4 sm:grid-cols-2 dark:border-stone-800"
        noValidate
        aria-label="Nouvelle catégorie"
        onSubmit={(e) => {
          e.preventDefault();
          const result = buildCategoryCreate(form, spaceId);
          if (!result.ok) return setInvalid(result.error);
          create.mutate(result.value);
        }}
      >
        <h3 className="text-sm font-semibold sm:col-span-2">Nouvelle catégorie</h3>
        <Field label="Nom">
          <Input
            value={form.name}
            maxLength={CATEGORY_NAME_MAX}
            autoComplete="off"
            onChange={(e) => update({ name: e.target.value })}
            required
          />
        </Field>
        <Field label="Type">
          <Select value={form.kind} onChange={(e) => update({ kind: e.target.value as CategoryKind })}>
            <option value="expense">Dépenses</option>
            <option value="income">Revenus</option>
          </Select>
        </Field>
        {form.kind === "expense" && (
          <Field label="Budget mensuel (facultatif)" hint="Ex. 300 ou 250,50">
            <Input inputMode="decimal" autoComplete="off" value={form.budget} onChange={(e) => update({ budget: e.target.value })} />
          </Field>
        )}
        <div className="flex items-end sm:col-span-2">
          <Button type="submit" variant="secondary" disabled={create.isPending}>
            {create.isPending ? "Ajout…" : "Ajouter la catégorie"}
          </Button>
        </div>
        {(invalid || create.error) && (
          <div className="sm:col-span-2">
            <Alert tone="error">{invalid ?? financeErrorMessage(create.error)}</Alert>
          </div>
        )}
      </form>
    </Card>
  );
}
