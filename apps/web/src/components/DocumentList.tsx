"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Alert, Badge, Button, Empty, Select, Spinner, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import {
  documentKeys,
  documentsPath,
  DOCUMENT_STATUS_LABELS,
  DOCUMENT_STATUS_TONES,
  emptyListMessage,
  formatFileSize,
  KIND_LABELS,
  listPollInterval,
  originalLabel,
  PURPOSE_FILTERS,
  PURPOSE_LABELS,
  SENSITIVITY_LABELS,
  STATUS_FILTERS,
  type PurposeFilter,
  type StatusFilter,
} from "@/lib/documents";
import { formatDate } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { DocumentStatus, DocumentSummary, Page } from "@/lib/types";

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  return <Badge tone={DOCUMENT_STATUS_TONES[status]}>{DOCUMENT_STATUS_LABELS[status]}</Badge>;
}

function DocumentRow({
  document,
  current,
  showPurpose,
  spaceLabel,
}: {
  document: DocumentSummary;
  current: boolean;
  showPurpose: boolean;
  /** Space name, shown when all spaces are listed. */
  spaceLabel?: string;
}) {
  return (
    <li>
      <Link
        href={`/documents?id=${encodeURIComponent(document.id)}`}
        aria-current={current ? "true" : undefined}
        className={cx(
          "block rounded-lg border bg-white p-3 shadow-sm transition-colors focus-visible:outline-2 focus-visible:outline-teal-600 dark:bg-stone-900",
          current
            ? "border-teal-600 ring-1 ring-teal-600 dark:border-teal-500 dark:ring-teal-500"
            : "border-stone-200 hover:border-stone-400 dark:border-stone-800 dark:hover:border-stone-600",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <h3 className="min-w-0 font-semibold break-words">{document.title}</h3>
          <DocumentStatusBadge status={document.status} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
          <Badge>{KIND_LABELS[document.kind] ?? document.kind}</Badge>
          {showPurpose && document.purpose !== "document" && (
            <Badge>{PURPOSE_LABELS[document.purpose] ?? document.purpose}</Badge>
          )}
          {spaceLabel && <Badge>{spaceLabel}</Badge>}
          {document.sensitivity.map((category) => (
            <Badge key={category} tone="violet">
              {SENSITIVITY_LABELS[category] ?? category}
            </Badge>
          ))}
          {document.local_only && <Badge tone="violet">Gardé en local</Badge>}
        </div>
        <p className="mt-1.5 text-xs text-muted">
          {formatDate(document.created_at)} · {formatFileSize(document.size_bytes)} · {originalLabel(document)}
        </p>
      </Link>
    </li>
  );
}

/** Filtered, paged list of the documents of the selected space (or of all spaces). */
export function DocumentList({ selectedId }: { selectedId: string | null }) {
  const { selected, spaceName } = useSpace();
  const [purpose, setPurpose] = useState<PurposeFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");

  const documents = useInfiniteQuery({
    queryKey: documentKeys.list(selected, purpose, status),
    queryFn: ({ pageParam }) =>
      api<Page<DocumentSummary>>(documentsPath({ spaceId: selected, purpose, status, cursor: pageParam })),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: (query) => listPollInterval(query.state.data?.pages.flatMap((p) => p.items)),
  });
  const items = documents.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <section aria-labelledby="documents-list-title" className="flex flex-col gap-3">
      <h2 id="documents-list-title" className="text-base font-semibold">
        Mes documents
      </h2>
      <div className="flex flex-wrap items-end gap-3">
        <fieldset className="text-sm">
          <legend className="sr-only">Type de document</legend>
          <div className="flex flex-wrap rounded-md border border-stone-300 bg-white p-0.5 dark:border-stone-700 dark:bg-stone-900">
            {PURPOSE_FILTERS.map((filter) => (
              <label
                key={filter.value}
                className={cx(
                  "cursor-pointer rounded px-2.5 py-1 font-medium has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-teal-700",
                  purpose === filter.value
                    ? "bg-teal-700 text-white"
                    : "text-stone-700 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800",
                )}
              >
                <input
                  type="radio"
                  name="documents-purpose-filter"
                  className="sr-only"
                  checked={purpose === filter.value}
                  onChange={() => setPurpose(filter.value)}
                />
                {filter.label}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-stone-600 dark:text-stone-400">Statut</span>
          <Select
            className="w-auto py-1"
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
          >
            {STATUS_FILTERS.map((filter) => (
              <option key={filter.value} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </Select>
        </label>
      </div>

      {documents.isPending ? (
        <Spinner />
      ) : documents.error && items.length === 0 ? (
        <Alert tone="error">{errorMessage(documents.error)}</Alert>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-stone-300 p-4 dark:border-stone-700">
          <Empty>{emptyListMessage(purpose, status)}</Empty>
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-2">
            {items.map((document) => (
              <DocumentRow
                key={document.id}
                document={document}
                current={document.id === selectedId}
                showPurpose={purpose === "all"}
                spaceLabel={selected ? undefined : spaceName(document.space_id)}
              />
            ))}
          </ul>
          {documents.isFetchNextPageError && <Alert tone="error">{errorMessage(documents.error)}</Alert>}
          {documents.hasNextPage && (
            <div>
              <Button
                variant="secondary"
                size="sm"
                disabled={documents.isFetchingNextPage}
                onClick={() => void documents.fetchNextPage()}
              >
                {documents.isFetchingNextPage ? "Chargement…" : "Charger plus"}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
