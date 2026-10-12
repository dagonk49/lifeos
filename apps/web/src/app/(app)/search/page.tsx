"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDate, splitSnippet } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { IndexStatus, SearchHit, SearchResult } from "@/lib/types";
import {
  INDEX_STATUS_KEY,
  WORDS_ONLY_NOTICE,
  indexInProgress,
  indexProgressLabel,
  matchBadge,
  searchHitHref,
  searchHitLabel,
} from "@/lib/usage";

const TYPES = [
  { value: "tasks", label: "Tâches" },
  { value: "journal", label: "Journal" },
  { value: "facts", label: "Faits" },
  { value: "memories", label: "Souvenirs" },
  { value: "goals", label: "Objectifs" },
  { value: "documents", label: "Documents" },
  { value: "messages", label: "Conversations" },
] as const;

function MatchBadge({ match }: { match: SearchHit["match"] }) {
  const badge = matchBadge(match);
  if (!badge) return null;
  return (
    <span title={badge.description}>
      <Badge tone={badge.tone}>{badge.label}</Badge>
      <span className="sr-only"> : {badge.description}</span>
    </span>
  );
}

/** Progress of the local semantic index (hidden without a local model). */
function IndexProgress({ status }: { status: IndexStatus | undefined }) {
  if (!status?.semantic) return null;
  return (
    <p className="text-xs text-stone-500">
      {indexProgressLabel(status)}
      {indexInProgress(status) && " · indexation en cours, les éléments récents seront bientôt trouvés par le sens"}
    </p>
  );
}

function Snippet({ text }: { text: string }) {
  return (
    <p className="text-sm text-stone-700 dark:text-stone-300">
      {splitSnippet(text).map((part, i) =>
        part.highlight ? (
          <mark key={i} className="rounded bg-amber-200 px-0.5 dark:bg-amber-700/60 dark:text-white">
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </p>
  );
}

export default function SearchPage() {
  const { selected, spaceName } = useSpace();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [types, setTypes] = useState<string[]>(TYPES.map((t) => t.value));
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const params = new URLSearchParams({ q: query });
  if (selected) params.set("space_id", selected);
  types.forEach((t) => params.append("types", t));
  if (from) params.set("from", from);
  if (to) params.set("to", to);

  const results = useQuery({
    queryKey: ["search", params.toString()],
    queryFn: () => api<SearchResult>(`/v1/search?${params.toString()}`),
    enabled: query.length >= 2 && types.length > 0,
  });
  const indexStatus = useQuery({
    queryKey: INDEX_STATUS_KEY,
    queryFn: () => api<IndexStatus>("/v1/search/status"),
    // Follow the indexing while it runs; a finished index is only checked again on focus.
    refetchInterval: (q) => (indexInProgress(q.state.data) ? 30_000 : false),
  });

  return (
    <>
      <PageHeader title="Recherche" />
      <Card>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(draft.trim());
          }}
        >
          <div className="flex gap-2">
            <Input
              aria-label="Rechercher"
              placeholder="Rechercher dans tâches, journal, mémoire, objectifs, conversations…"
              value={draft}
              minLength={2}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button type="submit">Rechercher</Button>
          </div>
          <fieldset className="flex flex-wrap gap-3 text-sm">
            <legend className="sr-only">Types</legend>
            {TYPES.map((t) => (
              <label key={t.value} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  className="accent-teal-700"
                  checked={types.includes(t.value)}
                  onChange={(e) =>
                    setTypes((current) =>
                      e.target.checked ? [...current, t.value] : current.filter((v) => v !== t.value),
                    )
                  }
                />
                {t.label}
              </label>
            ))}
          </fieldset>
          <div className="flex flex-wrap gap-3">
            <Field label="Depuis le">
              <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </Field>
            <Field label="Jusqu'au">
              <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </Field>
          </div>
        </form>
      </Card>
      <div className="mt-4 flex flex-col gap-2">
        {results.data && !results.data.semantic && <Alert tone="info">{WORDS_ONLY_NOTICE}</Alert>}
        {(!results.data || results.data.semantic) && <IndexProgress status={indexStatus.data} />}
        {!query ? null : types.length === 0 ? (
          <Alert tone="warning">Cochez au moins un type de contenu à rechercher.</Alert>
        ) : results.isPending ? (
          <Spinner />
        ) : results.error ? (
          <Alert tone="error">{errorMessage(results.error)}</Alert>
        ) : results.data.hits.length === 0 ? (
          <Card>
            <Empty>Aucun résultat pour « {query} ».</Empty>
          </Card>
        ) : (
          <ul className="flex flex-col gap-2">
            {results.data.hits.map((hit) => (
              <li key={`${hit.type}-${hit.id}`}>
                <Card>
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                    <Badge tone="teal">{searchHitLabel(hit.type)}</Badge>
                    <MatchBadge match={hit.match} />
                    <span>{formatDate(hit.date)}</span>
                    {!selected && <Badge>{spaceName(hit.space_id)}</Badge>}
                    <Link className="ml-auto text-teal-700 underline dark:text-teal-400" href={searchHitHref(hit)}>
                      Ouvrir
                    </Link>
                  </div>
                  {hit.type !== "message" && hit.type !== "journal" && (
                    <p className="font-medium">{hit.type === "fact" ? null : hit.title}</p>
                  )}
                  <Snippet text={hit.snippet} />
                </Card>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
