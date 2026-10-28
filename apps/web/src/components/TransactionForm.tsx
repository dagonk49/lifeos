"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Alert, Button, Field, Input, Select, Textarea, cx } from "@/components/ui";
import { api, isApiError, newIdempotencyKey } from "@/lib/api";
import {
  buildTransactionCreate,
  buildTransactionUpdate,
  categoriesPath,
  categoryChoices,
  categoryLabel,
  CURRENCIES,
  DIRECTION_LABELS,
  emptyTransactionForm,
  financeErrorMessage,
  financeKeys,
  firstError,
  idempotencyKeyFor,
  LABEL_MAX,
  MERCHANT_MAX,
  NOTES_MAX,
  suggestionHint,
  TRANSACTION_CHANGE_KEYS,
  transactionToForm,
  type Direction,
  type FieldErrors,
  type FormField,
  type PendingKey,
  type TransactionFormValues,
} from "@/lib/finance";
import { useSpace } from "@/lib/space";
import type { FinanceCategory, ReceiptProposal, Transaction } from "@/lib/types";

/** The receipt a new transaction is recorded from (UC02). */
export interface ReceiptLink {
  documentId: string;
  suggested?: ReceiptProposal["suggested_category"];
}

const DIRECTIONS: Direction[] = ["expense", "income"];

function Slot({
  field,
  errors,
  baseId,
  className,
  children,
}: {
  field: FormField;
  errors: FieldErrors;
  baseId: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      {children}
      {errors[field] && (
        <p id={`${baseId}-${field}-error`} className="text-xs font-medium text-red-700 dark:text-red-400">
          {errors[field]}
        </p>
      )}
    </div>
  );
}

/**
 * Creation (manual or from a receipt) and edition of a transaction. Nothing is recorded
 * before the owner submits; a creation is idempotent (a retry is never duplicated).
 */
export function TransactionForm({
  transaction,
  spaceId,
  defaultSpaceId,
  initial,
  receipt,
  recurringId,
  today,
  submitLabel,
  onSaved,
  onCancel,
}: {
  /** Edit mode. */
  transaction?: Transaction;
  /** Fixed space (a receipt's); otherwise the selected space, or one to choose. */
  spaceId?: string | null;
  /** Preselected space when one has to be chosen. */
  defaultSpaceId?: string | null;
  initial?: TransactionFormValues;
  receipt?: ReceiptLink;
  /** The recurring item this new operation pays (« Enregistrer cette échéance »). */
  recurringId?: string | null;
  /** Today in the owner's time zone: the latest allowed date. */
  today: string;
  submitLabel?: string;
  onSaved: (transaction: Transaction) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const { spaces, selected } = useSpace();
  const baseId = useId();
  const editing = transaction !== undefined;
  const fixedSpace = transaction?.space_id ?? spaceId ?? selected;
  const [chosenSpace, setChosenSpace] = useState(
    () => defaultSpaceId ?? (spaces.length === 1 ? (spaces[0]?.id ?? "") : ""),
  );
  const space = fixedSpace ?? (chosenSpace || null);
  const [form, setForm] = useState<TransactionFormValues>(
    () => initial ?? (transaction ? transactionToForm(transaction) : emptyTransactionForm(today)),
  );
  const [errors, setErrors] = useState<FieldErrors>({});
  const pendingKey = useRef<PendingKey | null>(null);

  // Edition also lists archived categories, so that a transaction's current one shows.
  const categories = useQuery({
    queryKey: financeKeys.categories(space, editing),
    queryFn: () => api<FinanceCategory[]>(categoriesPath(space, editing)),
    enabled: space !== null,
  });
  const choices = space ? categoryChoices(categories.data ?? [], space, form.direction, form.category_id || null) : [];

  const invalidate = () => {
    for (const queryKey of TRANSACTION_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
  };
  const saved = (result: Transaction) => {
    pendingKey.current = null;
    invalidate();
    onSaved(result);
  };
  const create = useMutation({
    mutationFn: ({ body, key }: { body: object; key: string }) =>
      api<Transaction>("/v1/finance/transactions", { method: "POST", body, headers: { "Idempotency-Key": key } }),
    onSuccess: saved,
  });
  const patch = useMutation({
    mutationFn: ({ id, body, version }: { id: string; body: object; version: number }) =>
      api<Transaction>(`/v1/finance/transactions/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body,
        headers: { "If-Match": String(version) },
      }),
    onSuccess: saved,
    onError: (error) => {
      // Changed elsewhere: reload the list so that the next attempt uses the new version.
      if (isApiError(error, "version_conflict")) invalidate();
    },
  });
  const pending = create.isPending || patch.isPending;
  const serverError = create.error ?? patch.error;

  const update = (change: Partial<TransactionFormValues>) => {
    setForm((current) => ({ ...current, ...change }));
    setErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(change)) delete next[key as FormField];
      return next;
    });
  };
  const setDirection = (direction: Direction) => {
    const current = categories.data?.find((c) => c.id === form.category_id);
    // A category only fits one direction: drop it when it no longer matches.
    const keep = !form.category_id || current?.kind === direction;
    update(keep ? { direction } : { direction, category_id: "" });
  };

  const fail = (found: FieldErrors) => {
    setErrors(found);
    const first = firstError(found);
    if (first) document.getElementById(`${baseId}-${first}`)?.focus();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (transaction) {
      const result = buildTransactionUpdate(transaction, form, today);
      if (!result.ok) return fail(result.errors);
      if (Object.keys(result.value).length === 0) return onCancel();
      patch.mutate({ id: transaction.id, body: result.value, version: transaction.version });
      return;
    }
    const result = buildTransactionCreate(form, { spaceId: space, today, receiptDocumentId: receipt?.documentId });
    if (!result.ok) return fail(result.errors);
    const body = recurringId ? { ...result.value, recurring_id: recurringId } : result.value;
    pendingKey.current = idempotencyKeyFor(pendingKey.current, body, newIdempotencyKey);
    create.mutate({ body, key: pendingKey.current.key });
  };

  const a11y = (field: FormField) => ({
    id: `${baseId}-${field}`,
    "aria-invalid": errors[field] ? true : undefined,
    "aria-describedby": errors[field] ? `${baseId}-${field}-error` : undefined,
  });
  const text =
    (key: keyof TransactionFormValues) =>
    (event: { target: { value: string } }) =>
      update({ [key]: event.target.value });

  const suggested = receipt?.suggested ?? null;
  const showsSuggestion = suggested !== null && form.category_id === suggested.id;
  const categoryHint = showsSuggestion
    ? (suggestionHint(suggested) ?? undefined)
    : editing
      ? "Choisir une catégorie l'apprend à LifeOS pour ce commerçant."
      : "Sans catégorie, LifeOS applique ce qu'il a appris pour ce commerçant. Choisir une catégorie la lui apprend.";
  const currencies: string[] = CURRENCIES.includes(form.currency as (typeof CURRENCIES)[number])
    ? [...CURRENCIES]
    : [...CURRENCIES, form.currency];

  return (
    <form className="grid gap-3 sm:grid-cols-2" noValidate onSubmit={submit}>
      {!fixedSpace && (
        <Slot field="space" errors={errors} baseId={baseId} className="sm:col-span-2">
          <Field label="Espace">
            <Select
              value={chosenSpace}
              onChange={(e) => {
                setChosenSpace(e.target.value);
                setErrors((current) => ({ ...current, space: undefined }));
                update({ category_id: "" });
              }}
              {...a11y("space")}
            >
              <option value="">Choisissez un espace</option>
              {spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
        </Slot>
      )}
      <Slot field="amount" errors={errors} baseId={baseId}>
        <Field label="Montant" hint="Ex. 12,50 ou 1 234,56">
          <Input
            inputMode="decimal"
            autoComplete="off"
            value={form.amount}
            onChange={text("amount")}
            required
            {...a11y("amount")}
          />
        </Field>
      </Slot>
      <Slot field="direction" errors={errors} baseId={baseId}>
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1 font-medium text-stone-700 dark:text-stone-300">Sens</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1 py-1.5">
            {DIRECTIONS.map((direction, index) => (
              <label key={direction} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name={`${baseId}-direction`}
                  className="accent-teal-700"
                  checked={form.direction === direction}
                  onChange={() => setDirection(direction)}
                  {...(index === 0 ? a11y("direction") : {})}
                />
                {DIRECTION_LABELS[direction]}
              </label>
            ))}
          </div>
        </fieldset>
      </Slot>
      <Slot field="tx_date" errors={errors} baseId={baseId}>
        <Field label="Date">
          <Input type="date" value={form.tx_date} max={today} onChange={text("tx_date")} required {...a11y("tx_date")} />
        </Field>
      </Slot>
      <Slot field="currency" errors={errors} baseId={baseId}>
        <Field label="Devise" hint="Seules les opérations en euros entrent dans le budget.">
          <Select value={form.currency} onChange={text("currency")} {...a11y("currency")}>
            {currencies.map((currency) => (
              <option key={currency} value={currency}>
                {currency}
              </option>
            ))}
          </Select>
        </Field>
      </Slot>
      <Slot field="merchant" errors={errors} baseId={baseId}>
        <Field label="Commerçant">
          <Input
            value={form.merchant}
            onChange={text("merchant")}
            maxLength={MERCHANT_MAX}
            autoComplete="off"
            {...a11y("merchant")}
          />
        </Field>
      </Slot>
      <Slot field="label" errors={errors} baseId={baseId}>
        <Field label="Libellé">
          <Input
            value={form.label}
            onChange={text("label")}
            maxLength={LABEL_MAX}
            autoComplete="off"
            {...a11y("label")}
          />
        </Field>
      </Slot>
      <Slot field="category_id" errors={errors} baseId={baseId} className="sm:col-span-2">
        <Field label="Catégorie" hint={categoryHint}>
          <Select
            value={form.category_id}
            onChange={text("category_id")}
            disabled={space === null || categories.isPending}
            {...a11y("category_id")}
          >
            <option value="">{editing ? "Non classée" : "Aucune"}</option>
            {choices.map((c: FinanceCategory) => (
              <option key={c.id} value={c.id}>
                {categoryLabel(c)}
                {c.id === suggested?.id ? " (suggérée)" : ""}
              </option>
            ))}
          </Select>
        </Field>
        {categories.error && (
          <p className="text-xs text-red-700 dark:text-red-400">
            Catégories indisponibles : {financeErrorMessage(categories.error)}
          </p>
        )}
        {space !== null && categories.data && choices.length === 0 && (
          <p className="text-xs text-stone-500">
            Aucune catégorie de {form.direction === "expense" ? "dépenses" : "revenus"} dans cet espace : créez-en dans
            « Catégories ».
          </p>
        )}
      </Slot>
      <Slot field="notes" errors={errors} baseId={baseId} className="sm:col-span-2">
        <Field label="Notes">
          <Textarea
            value={form.notes}
            onChange={text("notes")}
            maxLength={NOTES_MAX}
            className="min-h-16"
            {...a11y("notes")}
          />
        </Field>
      </Slot>
      <div className="flex flex-wrap gap-2 sm:col-span-2">
        <Button type="submit" disabled={pending}>
          {pending ? "Enregistrement…" : (submitLabel ?? (editing ? "Enregistrer les modifications" : "Ajouter l'opération"))}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={pending}>
          Annuler
        </Button>
      </div>
      {serverError && (
        <div className="sm:col-span-2">
          <Alert tone="error">{financeErrorMessage(serverError)}</Alert>
        </div>
      )}
    </form>
  );
}
