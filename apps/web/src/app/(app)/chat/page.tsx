"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Alert, Badge, Button, Card, Empty, PageHeader, Spinner, Textarea, cx } from "@/components/ui";
import { api, errorMessage, problemMessage } from "@/lib/api";
import { formatDate, formatTime } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type { Conversation, ConversationDetail, Message, Page, SendResult } from "@/lib/types";

function MessageBubble({ message, conversationId }: { message: Message; conversationId: string }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
  const retain = useMutation({
    mutationFn: () =>
      api(`/v1/chat/messages/${message.id}/retain`, { method: "POST", body: { retained: !message.retained } }),
    onSuccess: refresh,
  });
  const remember = useMutation({
    mutationFn: () => api(`/v1/chat/messages/${message.id}/remember`, { method: "POST" }),
    onSuccess: () => setNotice("Ajouté à la mémoire"),
  });
  const mine = message.role === "user";
  return (
    <li className={cx("flex flex-col gap-1", mine ? "items-end" : "items-start")}>
      <div
        className={cx(
          "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
          mine
            ? "bg-teal-700 text-white"
            : "border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900",
        )}
      >
        {/* Plain text only: assistant output is never rendered as HTML (threat model T14). */}
        {message.content}
      </div>
      <div className="flex items-center gap-2 text-xs text-stone-500">
        <span>{formatTime(message.created_at)}</span>
        {message.retained && <Badge tone="teal">Conservé</Badge>}
        <button className="underline" onClick={() => retain.mutate()}>
          {message.retained ? "Ne plus conserver" : "Conserver"}
        </button>
        <button className="underline" onClick={() => remember.mutate()}>
          Mémoriser
        </button>
        {notice && <span role="status">{notice}</span>}
      </div>
    </li>
  );
}

export default function ChatPage() {
  const queryClient = useQueryClient();
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [current, setCurrent] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [degraded, setDegraded] = useState<SendResult["error"]>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const bottom = useRef<HTMLDivElement>(null);

  const conversations = useQuery({
    queryKey: ["conversations", selected],
    queryFn: () => api<Page<Conversation>>(`/v1/chat/conversations${spaceParam(selected)}`),
  });
  const detail = useQuery({
    queryKey: ["conversation", current],
    queryFn: () => api<ConversationDetail>(`/v1/chat/conversations/${current}`),
    enabled: current !== null,
  });

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [detail.data?.messages.length]);

  useEffect(() => {
    if (startedAt === null) return;
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  const create = useMutation({
    mutationFn: () =>
      api<Conversation>("/v1/chat/conversations", { method: "POST", body: { space_id: writeSpaceId } }),
    onSuccess: (conversation) => {
      setCurrent(conversation.id);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const send = useMutation({
    mutationFn: async (content: string) => {
      let id = current;
      if (!id) {
        const conversation = await api<Conversation>("/v1/chat/conversations", {
          method: "POST",
          body: { space_id: writeSpaceId },
        });
        id = conversation.id;
        setCurrent(id);
      }
      setStartedAt(Date.now());
      setElapsed(0);
      return api<SendResult>(`/v1/chat/conversations/${id}/messages`, {
        method: "POST",
        idempotent: true,
        body: { content, context: { screen: "chat" } },
      });
    },
    onSuccess: (result) => {
      setDraft("");
      setDegraded(result.error);
    },
    onSettled: () => {
      setStartedAt(null);
      void queryClient.invalidateQueries({ queryKey: ["conversation"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/v1/chat/conversations/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setCurrent(null);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  return (
    <>
      <PageHeader title="Assistant">
        <Button variant="secondary" size="sm" onClick={() => create.mutate()}>
          Nouvelle conversation
        </Button>
      </PageHeader>
      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <Card title="Conversations" className="max-h-[70vh] overflow-y-auto">
          {conversations.isPending ? (
            <Spinner />
          ) : (conversations.data?.items.length ?? 0) === 0 ? (
            <Empty>Aucune conversation.</Empty>
          ) : (
            <ul className="flex flex-col gap-1">
              {conversations.data?.items.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => setCurrent(c.id)}
                    aria-current={c.id === current ? "true" : undefined}
                    className={cx(
                      "w-full rounded px-2 py-1.5 text-left text-sm",
                      c.id === current
                        ? "bg-teal-100 dark:bg-teal-900/50"
                        : "hover:bg-stone-100 dark:hover:bg-stone-800",
                    )}
                  >
                    <span className="line-clamp-1">{c.title}</span>
                    <span className="text-xs text-stone-500">
                      {formatDate(c.last_message_at ?? c.created_at)}
                      {!selected && ` · ${spaceName(c.space_id)}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title={detail.data?.title ?? "Nouvelle conversation"}
          actions={
            current && (
              <Button variant="ghost" size="sm" onClick={() => remove.mutate(current)}>
                Supprimer
              </Button>
            )
          }
          footer={`Espace : ${spaceName(detail.data?.space_id ?? writeSpaceId)} · conversations brutes conservées 90 jours sauf « Conserver »`}
        >
          <div className="flex min-h-[40vh] flex-col gap-3">
            <ol aria-label="Messages" className="flex max-h-[50vh] flex-1 flex-col gap-3 overflow-y-auto pr-1" aria-live="polite">
              {detail.data?.messages.map((m) => (
                <MessageBubble key={m.id} message={m} conversationId={detail.data.id} />
              ))}
              <div ref={bottom} />
            </ol>
            {send.isPending && <Spinner label={`L'assistant réfléchit… ${elapsed} s`} />}
            {degraded && (
              <Alert tone="warning">
                Message enregistré, réponse indisponible : {problemMessage(degraded.code, degraded.title)}
              </Alert>
            )}
            {send.error && <Alert tone="error">{errorMessage(send.error)}</Alert>}
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (draft.trim()) send.mutate(draft.trim());
              }}
            >
              <Textarea
                aria-label="Votre message"
                placeholder="Écrivez à LifeOS…"
                value={draft}
                maxLength={8000}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (draft.trim()) send.mutate(draft.trim());
                  }
                }}
              />
              <div className="flex justify-end">
                <Button type="submit" disabled={send.isPending || !draft.trim()}>
                  Envoyer
                </Button>
              </div>
            </form>
          </div>
        </Card>
      </div>
    </>
  );
}
