"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDate, splitSnippet } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { SearchHit } from "@/lib/types";

const TYPES = [
  { value: "tasks", label: "Tâches" },
  { value: "journal", label: "Journal" },
  { value: "facts", label: "Faits" },
  { value: "memories", label: "Souvenirs" },
  { value: "messages", label: "Conversations" },
] as const;

const HIT_LABELS: Record<SearchHit["type"], string> = {
  task: "Tâche",
  journal: "Journal",
  fact: "Fait",
  memory: "Souvenir",
  message: "Message",
};

const HIT_LINKS: Record<SearchHit["type"], string> = {
  task: "/tasks",
  journal: "/journal",
  fact: "/memory",
  memory: "/memory",
  message: "/chat",
};

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
    queryFn: () => api<{ query: string; hits: SearchHit[] }>(`/v1/search?${params.toString()}`),
    enabled: query.length >= 2 && types.length > 0,
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
              placeholder="Rechercher dans tâches, journal, mémoire, conversations…"
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
      <div className="mt-4">
        {!query ? null : results.isPending ? (
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
                    <Badge tone="teal">{HIT_LABELS[hit.type]}</Badge>
                    <span>{formatDate(hit.date)}</span>
                    {!selected && <Badge>{spaceName(hit.space_id)}</Badge>}
                    <Link className="ml-auto text-teal-700 underline dark:text-teal-400" href={HIT_LINKS[hit.type]}>
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
