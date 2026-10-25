"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { TaskRow } from "@/components/TaskRow";
import { Alert, Button, Card, Empty, Field, Input, PageHeader, Select, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { localInputToIso } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import { dependencyCandidates, knownTags, parseTags, sortTasks } from "@/lib/tasks";
import type { Page, Task, TaskPriority } from "@/lib/types";

const OPEN = "status=todo&status=in_progress";

type View = "open" | "blocked" | "done";

const VIEWS: Record<View, { query: string; empty: string }> = {
  open: { query: OPEN, empty: "Aucune tâche ouverte." },
  blocked: { query: `${OPEN}&blocked=true`, empty: "Aucune tâche bloquée." },
  done: { query: "status=done", empty: "Aucune tâche terminée." },
};

export default function TasksPage() {
  const queryClient = useQueryClient();
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [view, setView] = useState<View>("open");
  const [tag, setTag] = useState("");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [remind, setRemind] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("normal");
  const [dependsOn, setDependsOn] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [tagsError, setTagsError] = useState<string | null>(null);

  const load = (v: View) => () =>
    api<Page<Task>>(`/v1/tasks?${VIEWS[v].query}&limit=200${spaceParam(selected, "&")}`);
  // Open tasks: the default list, and what a task can be linked to.
  const openTasks = useQuery({ queryKey: ["tasks", selected, "open"], queryFn: load("open") });
  const otherTasks = useQuery({
    queryKey: ["tasks", selected, view],
    queryFn: load(view),
    enabled: view !== "open",
  });
  const tasks = view === "open" ? openTasks : otherTasks;
  const openItems = openTasks.data?.items ?? [];

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const create = useMutation({
    mutationFn: (tags: string[]) =>
      api<Task>("/v1/tasks", {
        method: "POST",
        idempotent: true,
        body: {
          space_id: writeSpaceId,
          title,
          priority,
          due_at: localInputToIso(due),
          remind_at: localInputToIso(remind),
          tags,
          depends_on: dependsOn ? [dependsOn] : [],
        },
      }),
    onSuccess: () => {
      setTitle("");
      setDue("");
      setRemind("");
      setPriority("normal");
      setDependsOn("");
      setTagsInput("");
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/v1/tasks/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });

  const items = tasks.data?.items ?? [];
  const tags = knownTags(items);
  const shown = sortTasks(tag ? items.filter((t) => t.tags.includes(tag)) : items);
  const newTaskCandidates = writeSpaceId ? dependencyCandidates(openItems, writeSpaceId) : [];

  return (
    <>
      <PageHeader title="Tâches" />
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card>
          <div className="mb-3 flex flex-col gap-3 sm:flex-row">
            <Field label="Afficher" className="sm:w-48">
              <Select value={view} onChange={(e) => setView(e.target.value as View)}>
                <option value="open">Tâches ouvertes</option>
                <option value="blocked">Tâches bloquées</option>
                <option value="done">Tâches terminées</option>
              </Select>
            </Field>
            {tags.length > 0 && (
              <Field label="Étiquette" className="sm:w-48">
                <Select value={tag} onChange={(e) => setTag(e.target.value)}>
                  <option value="">Toutes</option>
                  {tags.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </div>
          {tasks.isPending ? (
            <Spinner />
          ) : tasks.error ? (
            <Alert tone="error">{errorMessage(tasks.error)}</Alert>
          ) : shown.length === 0 ? (
            <Empty>{tag ? "Aucune tâche avec cette étiquette." : VIEWS[view].empty}</Empty>
          ) : (
            <ul className="divide-y divide-stone-100 dark:divide-stone-800">
              {shown.map((t) => (
                <TaskRow
                  key={t.id}
                  task={t}
                  onDelete={() => remove.mutate(t.id)}
                  candidates={dependencyCandidates(openItems, t.space_id, t)}
                />
              ))}
            </ul>
          )}
        </Card>
        <Card title="Nouvelle tâche" footer={`Espace : ${spaceName(writeSpaceId)}`}>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!title.trim()) return;
              const parsed = parseTags(tagsInput);
              if (!parsed.ok) {
                setTagsError(parsed.error);
                return;
              }
              create.mutate(parsed.tags);
            }}
          >
            <Field label="Intitulé">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
            </Field>
            <Field label="Échéance">
              <Input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
            </Field>
            <Field label="Rappel" hint="Notification, hors plages de tranquillité.">
              <Input type="datetime-local" value={remind} onChange={(e) => setRemind(e.target.value)} />
            </Field>
            <Field label="Priorité">
              <Select value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
                <option value="low">Basse</option>
                <option value="normal">Normale</option>
                <option value="high">Haute</option>
              </Select>
            </Field>
            <Field label="Étiquettes" hint="Séparées par des virgules, par exemple : maison, urgent.">
              <Input
                value={tagsInput}
                onChange={(e) => {
                  setTagsInput(e.target.value);
                  setTagsError(null);
                }}
                onBlur={() => {
                  const parsed = parseTags(tagsInput);
                  setTagsError(parsed.ok ? null : parsed.error);
                }}
                aria-invalid={tagsError !== null}
              />
            </Field>
            {tagsError && <Alert tone="error">{tagsError}</Alert>}
            {newTaskCandidates.length > 0 && (
              <Field label="Dépend de" hint="Elle restera bloquée tant que cette tâche est ouverte.">
                <Select value={dependsOn} onChange={(e) => setDependsOn(e.target.value)}>
                  <option value="">Aucune tâche</option>
                  {newTaskCandidates.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <Button type="submit" disabled={create.isPending || !title.trim()}>
              Ajouter
            </Button>
            {create.error && <Alert tone="error">{errorMessage(create.error)}</Alert>}
          </form>
        </Card>
      </div>
    </>
  );
}
