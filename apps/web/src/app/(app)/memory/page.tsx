"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Textarea,
  cx,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, KIND_LABELS, ORIGIN_LABELS } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { Fact, FactKind, Memory, MemoryRevision, Page } from "@/lib/types";

const ORIGIN_TONES = {
  user_declared: "teal",
  user_confirmed: "blue",
  extracted: "amber",
  inferred: "amber",
} as const;

function FactRow({ fact }: { fact: Fact }) {
  const queryClient = useQueryClient();
  const { selected, spaceName } = useSpace();
  const [correcting, setCorrecting] = useState(false);
  const [statement, setStatement] = useState(fact.statement);
  const [history, setHistory] = useState<Fact[] | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["facts"] });

  const correct = useMutation({
    mutationFn: () =>
      api<Fact>(`/v1/memory/facts/${fact.id}/correct`, { method: "POST", body: { statement } }),
    onSuccess: () => {
      setCorrecting(false);
      void refresh();
    },
  });
  const retract = useMutation({
    mutationFn: () => api(`/v1/memory/facts/${fact.id}/retract`, { method: "POST" }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/memory/facts/${fact.id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const loadHistory = useMutation({
    mutationFn: () => api<Fact[]>(`/v1/memory/facts/${fact.id}/history`),
    onSuccess: setHistory,
  });

  return (
    <li className="py-3">
      {correcting ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            correct.mutate();
          }}
        >
          <Input aria-label="Énoncé corrigé" value={statement} onChange={(e) => setStatement(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" type="submit">
              Enregistrer la correction
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setCorrecting(false)}>
              Annuler
            </Button>
          </div>
        </form>
      ) : (
        <p>{fact.statement}</p>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-stone-500">
        <Badge tone="violet">{KIND_LABELS[fact.kind]}</Badge>
        <Badge tone={ORIGIN_TONES[fact.origin]}>{ORIGIN_LABELS[fact.origin]}</Badge>
        <span>confiance {Math.round(Number(fact.confidence) * 100)} %</span>
        {fact.occurred_at && <span>· le {formatDate(fact.occurred_at)}</span>}
        <span>· source {fact.source_type}</span>
        <span>· {formatDateTime(fact.created_at)}</span>
        {!selected && <Badge>{spaceName(fact.space_id)}</Badge>}
      </div>
      {!correcting && (
        <div className="mt-1 flex flex-wrap gap-1">
          <Button size="sm" variant="ghost" onClick={() => setCorrecting(true)}>
            Corriger
          </Button>
          <Button size="sm" variant="ghost" onClick={() => loadHistory.mutate()}>
            Historique
          </Button>
          <Button size="sm" variant="ghost" onClick={() => retract.mutate()}>
            Retirer
          </Button>
          <Button size="sm" variant="ghost" onClick={() => remove.mutate()}>
            Effacer
          </Button>
        </div>
      )}
      {history && history.length > 1 && (
        <ol className="mt-2 border-l-2 border-stone-200 pl-3 text-xs text-stone-600 dark:border-stone-700">
          {history.slice(1).map((h) => (
            <li key={h.id}>
              <span className="line-through">{h.statement}</span> · {formatDateTime(h.created_at)}
            </li>
          ))}
        </ol>
      )}
      {history && history.length <= 1 && <p className="mt-1 text-xs text-stone-500">Jamais corrigé.</p>}
    </li>
  );
}

function MemoryCard({ memory }: { memory: Memory }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(memory.title);
  const [content, setContent] = useState(memory.content);
  const [note, setNote] = useState("");
  const [revisions, setRevisions] = useState<MemoryRevision[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["memories"] });

  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<Memory>(`/v1/memory/memories/${memory.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      setEditing(false);
      void refresh();
    },
    onError: (e) => setError(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (confirm: boolean) =>
      api(`/v1/memory/memories/${memory.id}${confirm ? "?confirm=true" : ""}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (e) => setError(errorMessage(e)),
  });
  const loadRevisions = useMutation({
    mutationFn: () => api<MemoryRevision[]>(`/v1/memory/memories/${memory.id}/revisions`),
    onSuccess: setRevisions,
  });

  return (
    <Card
      title={memory.title}
      actions={
        <>
          {memory.protected && <Badge tone="teal">Protégé</Badge>}
          <Badge>Importance {memory.importance}/5</Badge>
          <Badge tone={ORIGIN_TONES[memory.origin]}>{ORIGIN_LABELS[memory.origin]}</Badge>
        </>
      }
      footer={`Révision ${memory.revision} · modifié le ${formatDateTime(memory.updated_at)}`}
    >
      {editing ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            update.mutate({ title, content, change_note: note || null });
          }}
        >
          <Input aria-label="Titre" value={title} onChange={(e) => setTitle(e.target.value)} />
          <Textarea aria-label="Contenu" value={content} onChange={(e) => setContent(e.target.value)} />
          <Input aria-label="Motif de la modification" placeholder="Motif (facultatif)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" type="submit">
              Enregistrer
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>
              Annuler
            </Button>
          </div>
        </form>
      ) : (
        <p className="whitespace-pre-wrap text-sm">{memory.content}</p>
      )}
      {!editing && (
        <div className="mt-2 flex flex-wrap gap-1">
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            Modifier
          </Button>
          <Button size="sm" variant="ghost" onClick={() => update.mutate({ protected: !memory.protected })}>
            {memory.protected ? "Retirer la protection" : "Protéger"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => loadRevisions.mutate()}>
            Révisions
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (!memory.protected || window.confirm("Ce souvenir est protégé. Le supprimer définitivement ?")) {
                remove.mutate(memory.protected);
              }
            }}
          >
            Supprimer
          </Button>
        </div>
      )}
      {error && <Alert tone="error">{error}</Alert>}
      {revisions && (
        <ol className="mt-2 flex flex-col gap-1 border-l-2 border-stone-200 pl-3 text-xs dark:border-stone-700">
          {revisions.length === 0 && <li>Aucune révision antérieure.</li>}
          {revisions.map((r) => (
            <li key={r.revision}>
              <strong>Révision {r.revision}</strong> ({formatDateTime(r.changed_at)}
              {r.change_note && ` — ${r.change_note}`}) : {r.title} — {r.content.slice(0, 200)}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export default function MemoryPage() {
  const queryClient = useQueryClient();
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [tab, setTab] = useState<"facts" | "memories">("facts");
  const [statement, setStatement] = useState("");
  const [kind, setKind] = useState<FactKind>("fact");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [importance, setImportance] = useState("3");
  const [isProtected, setProtected] = useState(false);

  const facts = useQuery({
    queryKey: ["facts", selected],
    queryFn: () => api<Page<Fact>>(`/v1/memory/facts?limit=200${spaceParam(selected, "&")}`),
    enabled: tab === "facts",
  });
  const memories = useQuery({
    queryKey: ["memories", selected],
    queryFn: () => api<Page<Memory>>(`/v1/memory/memories?limit=200${spaceParam(selected, "&")}`),
    enabled: tab === "memories",
  });
  const addFact = useMutation({
    mutationFn: () =>
      api<Fact>("/v1/memory/facts", {
        method: "POST",
        idempotent: true,
        body: { space_id: writeSpaceId, kind, statement },
      }),
    onSuccess: () => {
      setStatement("");
      void queryClient.invalidateQueries({ queryKey: ["facts"] });
    },
  });
  const addMemory = useMutation({
    mutationFn: () =>
      api<Memory>("/v1/memory/memories", {
        method: "POST",
        idempotent: true,
        body: {
          space_id: writeSpaceId,
          title,
          content,
          importance: Number(importance),
          protected: isProtected,
        },
      }),
    onSuccess: () => {
      setTitle("");
      setContent("");
      setProtected(false);
      void queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
  });

  const tabClass = (active: boolean) =>
    cx(
      "rounded-md px-3 py-1.5 text-sm",
      active ? "bg-teal-100 font-medium text-teal-900 dark:bg-teal-900/50 dark:text-teal-100" : "hover:bg-stone-100 dark:hover:bg-stone-800",
    );

  return (
    <>
      <PageHeader title="Mémoire">
        <a
          href="/v1/privacy/export"
          download
          className="rounded-md border border-stone-300 px-2.5 py-1 text-sm hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          Exporter mes données (JSON)
        </a>
      </PageHeader>
      <div role="tablist" aria-label="Type de mémoire" className="mb-4 flex gap-2">
        <button role="tab" aria-selected={tab === "facts"} className={tabClass(tab === "facts")} onClick={() => setTab("facts")}>
          Faits
        </button>
        <button role="tab" aria-selected={tab === "memories"} className={tabClass(tab === "memories")} onClick={() => setTab("memories")}>
          Souvenirs
        </button>
      </div>

      {tab === "facts" ? (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <Card footer="« Déclaré » : dit par vous · « Confirmé » : proposé puis validé par vous · « Extrait »/« Inféré » : à vérifier">
            {facts.isPending ? (
              <Spinner />
            ) : facts.error ? (
              <Alert tone="error">{errorMessage(facts.error)}</Alert>
            ) : facts.data.items.length === 0 ? (
              <Empty>Aucun fait mémorisé.</Empty>
            ) : (
              <ul className="divide-y divide-stone-100 dark:divide-stone-800">
                {facts.data.items.map((f) => (
                  <FactRow key={f.id} fact={f} />
                ))}
              </ul>
            )}
          </Card>
          <Card title="Ajouter un fait" footer={`Espace : ${spaceName(writeSpaceId)}`}>
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (statement.trim()) addFact.mutate();
              }}
            >
              <Field label="Type">
                <Select value={kind} onChange={(e) => setKind(e.target.value as FactKind)}>
                  {Object.entries(KIND_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Énoncé">
                <Textarea value={statement} onChange={(e) => setStatement(e.target.value)} rows={3} required />
              </Field>
              <Button type="submit" disabled={!statement.trim() || addFact.isPending}>
                Ajouter
              </Button>
              {addFact.error && <Alert tone="error">{errorMessage(addFact.error)}</Alert>}
            </form>
          </Card>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <div className="flex flex-col gap-4">
            {memories.isPending ? (
              <Spinner />
            ) : memories.error ? (
              <Alert tone="error">{errorMessage(memories.error)}</Alert>
            ) : memories.data.items.length === 0 ? (
              <Card>
                <Empty>Aucun souvenir.</Empty>
              </Card>
            ) : (
              memories.data.items.map((m) => <MemoryCard key={m.id} memory={m} />)
            )}
          </div>
          <Card title="Nouveau souvenir" footer={`Espace : ${spaceName(writeSpaceId)}`}>
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (title.trim() && content.trim()) addMemory.mutate();
              }}
            >
              <Field label="Titre">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
              </Field>
              <Field label="Contenu">
                <Textarea value={content} onChange={(e) => setContent(e.target.value)} required />
              </Field>
              <Field label="Importance">
                <Select value={importance} onChange={(e) => setImportance(e.target.value)}>
                  {[1, 2, 3, 4, 5].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </Select>
              </Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="accent-teal-700" checked={isProtected} onChange={(e) => setProtected(e.target.checked)} />
                Protéger (jamais purgé automatiquement)
              </label>
              <Button type="submit" disabled={addMemory.isPending}>
                Enregistrer
              </Button>
              {addMemory.error && <Alert tone="error">{errorMessage(addMemory.error)}</Alert>}
            </form>
          </Card>
        </div>
      )}
    </>
  );
}
