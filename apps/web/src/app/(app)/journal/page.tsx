"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApprovalReview } from "@/components/ApprovalReview";
import { EraseConfirm } from "@/components/EraseConfirm";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Select, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDate, todayLocal } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { Approval, JournalEntry, Page } from "@/lib/types";
import { appendTranscript } from "@/lib/voice";

const MOODS = ["", "1 — très difficile", "2 — difficile", "3 — neutre", "4 — bien", "5 — très bien"];

function EntryCard({ entry }: { entry: JournalEntry }) {
  const queryClient = useQueryClient();
  const { selected, spaceName } = useSpace();
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(entry.content);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [erasing, setErasing] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["journal"] });

  const save = useMutation({
    mutationFn: () =>
      api<JournalEntry>(`/v1/journal/${entry.id}`, {
        method: "PATCH",
        body: { content },
        headers: { "If-Match": String(entry.version) },
      }),
    onSuccess: () => {
      setEditing(false);
      void refresh();
    },
  });
  const analyze = useMutation({
    mutationFn: () => api<Approval>(`/v1/journal/${entry.id}/analyze`, { method: "POST" }),
    onSuccess: setApproval,
  });

  return (
    <Card
      title={formatDate(entry.entry_date)}
      actions={
        <>
          {entry.mood && <Badge>Humeur {entry.mood}/5</Badge>}
          {!selected && <Badge>{spaceName(entry.space_id)}</Badge>}
          {entry.analyzed_at && <Badge tone="teal">Analysée</Badge>}
        </>
      }
    >
      {editing ? (
        <div className="flex flex-col gap-2">
          <Textarea aria-label="Contenu" value={content} onChange={(e) => setContent(e.target.value)} rows={6} />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
              Enregistrer
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>
              Annuler
            </Button>
          </div>
          {save.error && <Alert tone="error">{errorMessage(save.error)}</Alert>}
        </div>
      ) : (
        <p className="whitespace-pre-wrap text-sm">{entry.content}</p>
      )}
      {!editing && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" onClick={() => analyze.mutate()} disabled={analyze.isPending}>
            {analyze.isPending ? "Analyse…" : "Extraire tâches et faits"}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
            Modifier
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setErasing(true)} disabled={erasing}>
            Supprimer
          </Button>
        </div>
      )}
      {erasing && (
        <EraseConfirm
          question={`Supprimer l'entrée du ${formatDate(entry.entry_date)} ?`}
          confirmLabel="Supprimer l'entrée"
          deletePath={`/v1/journal/${entry.id}`}
          derivedPath={`/v1/journal/${entry.id}/derived`}
          onDone={(withDerived) => {
            setErasing(false);
            void refresh();
            if (withDerived) {
              void queryClient.invalidateQueries({ queryKey: ["tasks"] });
              void queryClient.invalidateQueries({ queryKey: ["memory"] });
            }
          }}
          onCancel={() => setErasing(false)}
        />
      )}
      {analyze.error && (
        <div className="mt-2">
          <Alert tone="warning">{errorMessage(analyze.error)}</Alert>
        </div>
      )}
      {approval && (
        <div className="mt-3">
          <ApprovalReview approval={approval} onDone={() => setApproval(null)} />
        </div>
      )}
    </Card>
  );
}

export default function JournalPage() {
  const queryClient = useQueryClient();
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [date, setDate] = useState(todayLocal());
  const [content, setContent] = useState("");
  const [mood, setMood] = useState("");
  const [dictated, setDictated] = useState(false);

  const entries = useQuery({
    queryKey: ["journal", selected],
    queryFn: () => api<Page<JournalEntry>>(`/v1/journal${spaceParam(selected)}`),
  });
  const create = useMutation({
    mutationFn: () =>
      api<JournalEntry>("/v1/journal", {
        method: "POST",
        idempotent: true,
        body: { space_id: writeSpaceId, entry_date: date, content, mood: mood ? Number(mood) : null },
      }),
    onSuccess: () => {
      setContent("");
      setMood("");
      setDictated(false);
      void queryClient.invalidateQueries({ queryKey: ["journal"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  return (
    <>
      <PageHeader title="Journal" />
      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Card title="Ma journée" footer={`Espace : ${spaceName(writeSpaceId)}`}>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (content.trim()) create.mutate();
            }}
          >
            <Field label="Date">
              <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
            </Field>
            <Field label="Faits, accomplissements, difficultés…">
              <Textarea value={content} onChange={(e) => setContent(e.target.value)} rows={8} required />
            </Field>
            {/* UC01: dictate the day; the transcript is reviewed here before being saved. */}
            <VoiceRecorder
              spaceId={writeSpaceId}
              label="Dicter ma journée"
              onTranscript={(text) => {
                setContent((current) => appendTranscript(current, text));
                setDictated(true);
              }}
            />
            <Field label="Humeur (facultatif)" hint="Partagée seulement si vous le souhaitez.">
              <Select value={mood} onChange={(e) => setMood(e.target.value)}>
                {MOODS.map((label, i) => (
                  <option key={i} value={i === 0 ? "" : String(i)}>
                    {label || "Non précisée"}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" disabled={create.isPending || !content.trim()}>
              Enregistrer
            </Button>
            {create.error && <Alert tone="error">{errorMessage(create.error)}</Alert>}
            {dictated && (
              <p className="text-xs text-stone-500">
                Après l'enregistrement, « Extraire tâches et faits » propose les tâches et
                accomplissements dictés : rien n'est ajouté sans votre validation.
              </p>
            )}
          </form>
        </Card>
        <div className="flex flex-col gap-4">
          {entries.isPending ? (
            <Spinner />
          ) : entries.error ? (
            <Alert tone="error">{errorMessage(entries.error)}</Alert>
          ) : entries.data.items.length === 0 ? (
            <Card>
              <Empty>Aucune entrée pour le moment.</Empty>
            </Card>
          ) : (
            entries.data.items.map((entry) => <EntryCard key={entry.id} entry={entry} />)
          )}
        </div>
      </div>
    </>
  );
}
