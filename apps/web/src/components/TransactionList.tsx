"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { TransactionForm } from "@/components/TransactionForm";
import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Spinner, cx } from "@/components/ui";
import { api, isApiError } from "@/lib/api";
import {
  categoriesPath,
  categoryChoices,
  categoryFilterOptions,
  categoryLabel,
  countLabel,
  documentHref,
  financeErrorMessage,
  financeKeys,
  isAutoCategorised,
  monthLabel,
  ofMonth,
  signedAmount,
  SOURCE_LABELS,
  TRANSACTION_CHANGE_KEYS,
  transactionsPath,
  transactionSubtitle,
  transactionTitle,
  type Direction,
  type TransactionFilters,
} from "@/lib/finance";
import { formatDate } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { FinanceCategory, Page, Transaction } from "@/lib/types";

type CategoryPatch = { tx: Transaction; categoryId: string | null };

function TransactionRow({
  tx,
  categories,
  categoriesReady,
  spaceLabel,
  today,
  highlighted,
  onChanged,
}: {
  tx: Transaction;
  categories: FinanceCategory[];
  /** False while the categories load: the current one is not known yet. */
  categoriesReady: boolean;
  spaceLabel?: string;
  today: string;
  highlighted: boolean;
  onChanged: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const item = useRef<HTMLLIElement>(null);
  const title = transactionTitle(tx);
  const subtitle = transactionSubtitle(tx);
  const choices = categoryChoices(categories, tx.space_id, tx.direction, tx.category_id);
  const unknownCurrent = tx.category_id !== null && !choices.some((c) => c.id === tx.category_id);
  const auto = isAutoCategorised(tx);

  useEffect(() => {
    if (highlighted && item.current) {
      item.current.scrollIntoView?.({ block: "center" });
      item.current.focus({ preventScroll: true });
    }
  }, [highlighted]);

  const invalidate = () => {
    for (const queryKey of TRANSACTION_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
  };
  const setCategory = useMutation({
    mutationFn: ({ tx: target, categoryId }: CategoryPatch) =>
      api<Transaction>(`/v1/finance/transactions/${encodeURIComponent(target.id)}`, {
        method: "PATCH",
        body: { category_id: categoryId },
        headers: { "If-Match": String(target.version) },
      }),
    onSuccess: (updated, { categoryId }) => {
      const name = categories.find((c) => c.id === categoryId)?.name;
      if (!categoryId) return onChanged(`Opération « ${title} » sans catégorie.`);
      const done = `Opération « ${title} » classée dans « ${name ?? "cette catégorie"} ».`;
      onChanged(updated.merchant ? `${done} LifeOS retiendra ce choix pour ce commerçant.` : done);
    },
    onSettled: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/finance/transactions/${encodeURIComponent(tx.id)}`, { method: "DELETE" }),
    onSuccess: () => {
      onChanged(`Opération « ${title} » supprimée.`);
      invalidate();
    },
  });
  const error = setCategory.error ?? remove.error;

  if (editing) {
    return (
      <li className="rounded-md border border-teal-300 p-3 dark:border-teal-800">
        <h3 className="mb-2 text-sm font-semibold">Modifier « {title} »</h3>
        <TransactionForm
          transaction={tx}
          today={today}
          onSaved={() => {
            setEditing(false);
            onChanged(`Opération « ${title} » modifiée.`);
          }}
          onCancel={() => setEditing(false)}
        />
      </li>
    );
  }

  return (
    <li
      ref={item}
      id={`transaction-${tx.id}`}
      tabIndex={-1}
      className={cx(
        "rounded-md border p-3 outline-none focus-visible:ring-2 focus-visible:ring-teal-600",
        highlighted
          ? "border-teal-400 bg-teal-50/60 dark:border-teal-700 dark:bg-teal-950/40"
          : "border-stone-200 dark:border-stone-800",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium break-words">{title}</p>
          <p className="text-xs text-stone-500">
            {formatDate(tx.tx_date)}
            {subtitle && ` · ${subtitle}`}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {spaceLabel && <Badge>{spaceLabel}</Badge>}
            {tx.source_type === "receipt" && tx.document_id ? (
              <Link
                href={documentHref(tx.document_id)}
                aria-label={`Ticket de caisse de « ${title} »`}
                className="rounded bg-sky-100 px-1.5 py-0.5 text-xs font-medium text-sky-800 underline-offset-2 hover:underline dark:bg-sky-900/50 dark:text-sky-200"
              >
                Ticket
              </Link>
            ) : (
              tx.source_type !== "manual" && <Badge tone="blue">{SOURCE_LABELS[tx.source_type]}</Badge>
            )}
            {tx.currency !== "EUR" && <Badge tone="amber">{tx.currency}, hors budget</Badge>}
          </div>
        </div>
        <p
          className={cx(
            "shrink-0 text-right font-semibold tabular-nums",
            tx.direction === "income" ? "text-teal-700 dark:text-teal-400" : "text-stone-900 dark:text-stone-100",
          )}
        >
          {signedAmount(tx)}
        </p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="flex min-w-0 items-center gap-2 text-sm">
          <span className="sr-only">Catégorie de « {title} »</span>
          <Select
            className="w-auto max-w-56 py-1"
            value={tx.category_id ?? ""}
            disabled={setCategory.isPending || !categoriesReady}
            onChange={(e) => setCategory.mutate({ tx, categoryId: e.target.value || null })}
          >
            <option value="">Non classée</option>
            {unknownCurrent && tx.category_id && (
              <option value={tx.category_id}>{categoriesReady ? "Catégorie indisponible" : "Chargement…"}</option>
            )}
            {choices.map((c) => (
              <option key={c.id} value={c.id}>
                {categoryLabel(c)}
              </option>
            ))}
          </Select>
        </label>
        {auto && (
          <>
            <Badge tone="violet">classée automatiquement</Badge>
            <Button
              size="sm"
              variant="ghost"
              disabled={setCategory.isPending}
              aria-label={`Confirmer la catégorie de « ${title} »`}
              onClick={() => setCategory.mutate({ tx, categoryId: tx.category_id })}
            >
              Confirmer
            </Button>
          </>
        )}
        <span className="ml-auto flex gap-1">
          <Button size="sm" variant="secondary" aria-label={`Modifier « ${title} »`} onClick={() => setEditing(true)}>
            Modifier
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Supprimer « ${title} »`}
            disabled={remove.isPending}
            onClick={() => {
              const receipt = tx.document_id ? " Le ticket reste dans vos documents." : "";
              if (window.confirm(`Supprimer l'opération « ${title} » (${signedAmount(tx)}) du ${formatDate(tx.tx_date)} ?${receipt}`)) {
                remove.mutate();
              }
            }}
          >
            Supprimer
          </Button>
        </span>
      </div>
      {tx.notes && <p className="mt-2 text-sm whitespace-pre-wrap text-stone-600 dark:text-stone-400">{tx.notes}</p>}
      {error && (
        <div className="mt-2">
          <Alert tone="error">
            {isApiError(error, "version_conflict")
              ? "Cette opération a été modifiée ailleurs : la liste est rechargée, réessayez."
              : financeErrorMessage(error)}
          </Alert>
        </div>
      )}
    </li>
  );
}

/** Transactions of the month (all spaces when none is selected), filters and inline edits. */
export function TransactionList({
  month,
  today,
  highlightId,
}: {
  month: string;
  today: string;
  /** Transaction to scroll to (just recorded, or already recorded from a receipt). */
  highlightId?: string | null;
}) {
  const { selected, spaceName } = useSpace();
  const [direction, setDirection] = useState<Direction | "">("");
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const filters: TransactionFilters = { spaceId: selected, month, direction, category, q };
  const filtered = direction !== "" || category !== "" || q !== "";

  const categories = useQuery({
    queryKey: financeKeys.categories(selected, true),
    queryFn: () => api<FinanceCategory[]>(categoriesPath(selected, true)),
  });
  const transactions = useInfiniteQuery({
    queryKey: financeKeys.transactions(filters),
    queryFn: ({ pageParam }) => api<Page<Transaction>>(transactionsPath(filters, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = transactions.data?.pages.flatMap((page) => page.items) ?? [];
  const options = categoryFilterOptions(categories.data ?? [], selected ? undefined : spaceName);

  return (
    <Card title="Opérations du mois">
      <form
        role="search"
        aria-label="Filtrer les opérations"
        className="mb-3 grid gap-2 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(search.trim());
        }}
      >
        <Field label="Sens">
          <Select value={direction} onChange={(e) => setDirection(e.target.value as Direction | "")}>
            <option value="">Dépenses et revenus</option>
            <option value="expense">Dépenses</option>
            <option value="income">Revenus</option>
          </Select>
        </Field>
        <Field label="Catégorie">
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex items-end gap-2 sm:col-span-2">
          <Field label="Rechercher" className="flex-1">
            <Input
              type="search"
              value={search}
              maxLength={200}
              placeholder="Commerçant, libellé, notes…"
              onChange={(e) => setSearch(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="secondary">
            Rechercher
          </Button>
          {filtered && (
            <Button
              variant="ghost"
              onClick={() => {
                setDirection("");
                setCategory("");
                setSearch("");
                setQ("");
              }}
            >
              Effacer les filtres
            </Button>
          )}
        </div>
      </form>

      <p className="mb-3 text-xs text-stone-500">
        Changer la catégorie d&apos;une opération : LifeOS retiendra ce choix pour ce commerçant.
      </p>
      {notice && (
        <div className="mb-3">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}

      {transactions.isPending ? (
        <Spinner />
      ) : !transactions.data ? (
        <Alert tone="error">{financeErrorMessage(transactions.error)}</Alert>
      ) : items.length === 0 ? (
        <Empty>
          {filtered
            ? "Aucune opération ne correspond à ces filtres."
            : `Aucune opération en ${monthLabel(month)}. Ajoutez-en une ou scannez un ticket.`}
        </Empty>
      ) : (
        <>
          <ul className="flex flex-col gap-2" aria-label={`Opérations ${ofMonth(month)}`}>
            {items.map((tx) => (
              <TransactionRow
                key={tx.id}
                tx={tx}
                categories={categories.data ?? []}
                categoriesReady={categories.data !== undefined}
                spaceLabel={selected ? undefined : spaceName(tx.space_id)}
                today={today}
                highlighted={tx.id === highlightId}
                onChanged={setNotice}
              />
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500">
            <span>
              {countLabel(items.length, "opération affichée", "opérations affichées")}
              {transactions.hasNextPage ? " (il y en a d'autres)" : ""}
            </span>
            {transactions.hasNextPage && (
              <Button
                size="sm"
                variant="secondary"
                disabled={transactions.isFetchingNextPage}
                onClick={() => void transactions.fetchNextPage()}
              >
                {transactions.isFetchingNextPage ? "Chargement…" : "Charger plus"}
              </Button>
            )}
          </div>
          {transactions.isFetchNextPageError && (
            <Alert tone="error">{financeErrorMessage(transactions.error)}</Alert>
          )}
        </>
      )}
    </Card>
  );
}
