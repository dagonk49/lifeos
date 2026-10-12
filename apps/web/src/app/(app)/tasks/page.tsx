"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { TaskRow } from "@/components/TaskRow";
import { Alert, Button, Card, Empty, Field, Input, PageHeader, Select, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { localInputToIso } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { Page, Task, TaskPriority } from "@/lib/types";

const OPEN = "status=todo&status=in_progress";

export default function TasksPage() {
  const queryClient = useQueryClient();
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [showDone, setShowDone] = useState(false);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [remind, setRemind] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("normal");

  const filter = showDone ? "status=done" : OPEN;
  const tasks = useQuery({
    queryKey: ["tasks", selected, showDone],
    queryFn: () => api<Page<Task>>(`/v1/tasks?${filter}&limit=200${spaceParam(selected, "&")}`),
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const create = useMutation({
    mutationFn: () =>
      api<Task>("/v1/tasks", {
        method: "POST",
        idempotent: true,
        body: {
          space_id: writeSpaceId,
          title,
          priority,
          due_at: localInputToIso(due),
          remind_at: localInputToIso(remind),
        },
      }),
    onSuccess: () => {
      setTitle("");
      setDue("");
      setRemind("");
      setPriority("normal");
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/v1/tasks/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });

  const sorted = [...(tasks.data?.items ?? [])].sort((a, b) => {
    if (a.due_at && b.due_at) return a.due_at.localeCompare(b.due_at);
    if (a.due_at) return -1;
    if (b.due_at) return 1;
    return b.created_at.localeCompare(a.created_at);
  });

  return (
    <>
      <PageHeader title="Tâches">
        <Button variant="secondary" size="sm" onClick={() => setShowDone((v) => !v)}>
          {showDone ? "Voir les tâches ouvertes" : "Voir les tâches terminées"}
        </Button>
      </PageHeader>
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card>
          {tasks.isPending ? (
            <Spinner />
          ) : tasks.error ? (
            <Alert tone="error">{errorMessage(tasks.error)}</Alert>
          ) : sorted.length === 0 ? (
            <Empty>{showDone ? "Aucune tâche terminée." : "Aucune tâche ouverte."}</Empty>
          ) : (
            <ul className="divide-y divide-stone-100 dark:divide-stone-800">
              {sorted.map((t) => (
                <TaskRow key={t.id} task={t} onDelete={() => remove.mutate(t.id)} />
              ))}
            </ul>
          )}
        </Card>
        <Card title="Nouvelle tâche" footer={`Espace : ${spaceName(writeSpaceId)}`}>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (title.trim()) create.mutate();
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
