"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { AgentPermissionCard, type AgentPermissionNeed } from "@/components/AgentPermissionCard";
import { AgentSelector } from "@/components/AgentSelector";
import { AgentSuggestion } from "@/components/AgentSuggestion";
import { ChatAttachments } from "@/components/ChatAttachments";
import { ConsolidationPanel } from "@/components/ConsolidationPanel";
import { SpeakButton } from "@/components/SpeakButton";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { Alert, Badge, Button, Card, Empty, PageHeader, Spinner, Textarea, cx } from "@/components/ui";
import { CORE_AGENT, agentLabel, agentsKey, missingPermissions, suggestedAgent, type AgentChoice } from "@/lib/agents";
import { api, errorMessage, problemMessage } from "@/lib/api";
import { attachmentState, attachmentsReading, sendableAttachments } from "@/lib/attachments";
import { formatDate, formatTime } from "@/lib/format";
import { spaceParam, useSpace } from "@/lib/space";
import type {
  AgentInfo,
  AgentName,
  Conversation,
  ConversationDetail,
  DocumentSummary,
  Message,
  Page,
  SendResult,
} from "@/lib/types";
import { appendTranscript } from "@/lib/voice";

function MessageBubble({
  message,
  conversationId,
  agents,
}: {
  message: Message;
  conversationId: string;
  agents: AgentInfo[] | undefined;
}) {
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
      {mine && (message.attachments?.length ?? 0) > 0 && (
        <ul aria-label="Pièces jointes envoyées" className="flex flex-wrap gap-2 text-xs">
          {message.attachments?.map((id, index) => (
            <li key={id}>
              <Link className="underline" href={`/documents?id=${encodeURIComponent(id)}`}>
                Pièce jointe {index + 1}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
        {!mine && message.agent && (
          <Badge tone="violet">
            <span className="sr-only">Agent : </span>
            {agentLabel(message.agent, agents)}
          </Badge>
        )}
        <span>{formatTime(message.created_at)}</span>
        {!mine && <SpeakButton text={message.content} />}
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

interface SendVars {
  content: string;
  agent: AgentChoice;
  /** Documents whose extracted text goes with the message. */
  attachments?: string[];
  /** Resending a message after granting access: the draft is left untouched. */
  resend?: boolean;
}

export default function ChatPage() {
  const queryClient = useQueryClient();
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [current, setCurrent] = useState<string | null>(null);
  const [currentSpace, setCurrentSpace] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // Specialised agents are only used when explicitly chosen here (F08).
  const [agent, setAgent] = useState<AgentChoice>(CORE_AGENT);
  const [degraded, setDegraded] = useState<SendResult["error"]>(null);
  const [permission, setPermission] = useState<(AgentPermissionNeed & { messageId: string }) | null>(null);
  const [suggestion, setSuggestion] = useState<AgentName | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
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
  // Agents are described for the conversation's space (a new conversation uses writeSpaceId).
  const spaceId = current ? (detail.data?.space_id ?? currentSpace) : writeSpaceId;
  const agents = useQuery({
    queryKey: agentsKey(spaceId),
    queryFn: () => api<AgentInfo[]>(`/v1/agents?space_id=${encodeURIComponent(spaceId ?? "")}`),
    enabled: Boolean(spaceId),
  });

  // Attached documents are read on the server: polled until ready (or refused).
  const attachmentQueries = useQueries({
    queries: attachmentIds.map((id) => ({
      queryKey: ["document", id],
      queryFn: () => api<DocumentSummary>(`/v1/documents/${id}`),
      refetchInterval: (query: { state: { data?: DocumentSummary } }) =>
        attachmentState(query.state.data) === "reading" ? 1500 : false,
    })),
  });
  const attachmentDocs = attachmentQueries.map((q) => q.data);
  const readingAttachments = attachmentsReading(attachmentDocs);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [detail.data?.messages.length]);

  useEffect(() => {
    if (startedAt === null) return;
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  /** Opens a conversation (or none). An existing one starts with the general assistant;
   * a new one keeps the agent the owner may already have picked while it was created. */
  function openConversation(id: string | null, space: string | null, { keepAgent = false } = {}) {
    setCurrent(id);
    setCurrentSpace(space);
    if (!keepAgent) setAgent(CORE_AGENT);
    setDegraded(null);
    setPermission(null);
    setSuggestion(null);
    if (!keepAgent) setAttachmentIds([]);
  }

  const create = useMutation({
    mutationFn: () =>
      api<Conversation>("/v1/chat/conversations", { method: "POST", body: { space_id: writeSpaceId } }),
    onSuccess: (conversation) => {
      openConversation(conversation.id, conversation.space_id, { keepAgent: true });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const send = useMutation({
    mutationFn: async ({ content, agent: chosen, attachments = [] }: SendVars) => {
      let id = current;
      let space = spaceId;
      if (!id) {
        const conversation = await api<Conversation>("/v1/chat/conversations", {
          method: "POST",
          body: { space_id: writeSpaceId },
        });
        id = conversation.id;
        space = conversation.space_id;
        setCurrent(id);
        setCurrentSpace(space);
      }
      setStartedAt(Date.now());
      setElapsed(0);
      const result = await api<SendResult>(`/v1/chat/conversations/${id}/messages`, {
        method: "POST",
        idempotent: true,
        body: { content, context: { screen: "chat" }, agent: chosen, attachments },
      });
      return { result, spaceId: space ?? writeSpaceId };
    },
    onMutate: ({ resend }) => {
      if (!resend) setPermission(null);
      setSuggestion(null);
    },
    onSuccess: ({ result, spaceId: space }, { content, resend }) => {
      if (!resend) {
        setDraft("");
        setAttachmentIds([]);
      }
      const missing = missingPermissions(result);
      if (missing !== null && result.agent !== "core") {
        // The message is saved; the agent first needs read access in this space.
        setDegraded(null);
        setPermission({ agent: result.agent, spaceId: space, missing, content, messageId: result.user_message.id });
      } else {
        setDegraded(result.error);
        setPermission(null);
      }
      setSuggestion(suggestedAgent(result));
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
      openConversation(null, null);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const submit = () => {
    const content = draft.trim();
    // Wait for a conversation being created: sending now would open a second one.
    if (content && !send.isPending && !create.isPending && !readingAttachments) {
      send.mutate({ content, agent, attachments: sendableAttachments(attachmentDocs) });
    }
  };

  return (
    <>
      <PageHeader title="Assistant">
        <Button variant="secondary" size="sm" onClick={() => create.mutate()} disabled={create.isPending}>
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
                    onClick={() => {
                      if (c.id !== current) openConversation(c.id, c.space_id);
                    }}
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
            {current && detail.data?.id === current && detail.data.messages.length > 0 && (
              <ConsolidationPanel key={detail.data.id} conversation={detail.data} />
            )}
            <ol aria-label="Messages" className="flex max-h-[50vh] flex-1 flex-col gap-3 overflow-y-auto pr-1" aria-live="polite">
              {detail.data?.messages.map((m) => (
                <MessageBubble key={m.id} message={m} conversationId={detail.data.id} agents={agents.data} />
              ))}
              <div ref={bottom} />
            </ol>
            {send.isPending && <Spinner label={`L'assistant réfléchit… ${elapsed} s`} />}
            {degraded && (
              <Alert tone="warning">
                Message enregistré, réponse indisponible : {problemMessage(degraded.code, degraded.title)}
              </Alert>
            )}
            {permission && (
              <AgentPermissionCard
                key={permission.messageId}
                need={permission}
                label={agentLabel(permission.agent, agents.data)}
                spaceName={spaceName(permission.spaceId)}
                resending={send.isPending}
                onResend={() => {
                  setAgent(permission.agent);
                  send.mutate({ content: permission.content, agent: permission.agent, resend: true });
                }}
                onDismiss={() => setPermission(null)}
              />
            )}
            {send.error && <Alert tone="error">{errorMessage(send.error)}</Alert>}
            {suggestion && suggestion !== agent && (
              <AgentSuggestion
                label={agentLabel(suggestion, agents.data)}
                onUse={() => {
                  setAgent(suggestion);
                  setSuggestion(null);
                }}
                onDismiss={() => setSuggestion(null)}
              />
            )}
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              <ChatAttachments
                spaceId={spaceId ?? writeSpaceId}
                conversationId={current}
                ids={attachmentIds}
                documents={attachmentDocs}
                onAdd={(id) => setAttachmentIds((ids) => (ids.includes(id) ? ids : [...ids, id]))}
                onRemove={(id) => setAttachmentIds((ids) => ids.filter((other) => other !== id))}
              />
              <VoiceRecorder
                spaceId={spaceId ?? writeSpaceId}
                onTranscript={(text) => setDraft((current) => appendTranscript(current, text, { multiline: false }))}
              />
              <Textarea
                aria-label="Votre message"
                placeholder="Écrivez à LifeOS…"
                value={draft}
                maxLength={8000}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <AgentSelector
                  agents={agents.data}
                  value={agent}
                  onChange={setAgent}
                  spaceName={spaceName(spaceId ?? writeSpaceId)}
                  error={agents.error}
                />
                <Button
                  type="submit"
                  className="self-end sm:mt-6"
                  disabled={send.isPending || create.isPending || readingAttachments || !draft.trim()}
                >
                  {readingAttachments ? "Lecture de la pièce jointe…" : "Envoyer"}
                </Button>
              </div>
            </form>
          </div>
        </Card>
      </div>
    </>
  );
}
