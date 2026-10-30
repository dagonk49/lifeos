"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { AgentPermissionCard, type AgentPermissionNeed } from "@/components/AgentPermissionCard";
import { AgentSelector } from "@/components/AgentSelector";
import { AgentSuggestion } from "@/components/AgentSuggestion";
import { ChatAttachments } from "@/components/ChatAttachments";
import { ConsolidationPanel } from "@/components/ConsolidationPanel";
import { EraseConfirm } from "@/components/EraseConfirm";
import { SendReviewPanel } from "@/components/SendReview";
import { SpeakButton } from "@/components/SpeakButton";
import { VoiceRecorder } from "@/components/VoiceRecorder";
import { Alert, Badge, Button, Card, Empty, Field, PageHeader, Spinner, Textarea, cx } from "@/components/ui";
import { CORE_AGENT, agentLabel, agentsKey, missingPermissions, suggestedAgent, type AgentChoice } from "@/lib/agents";
import { api, errorMessage, isApiError, problemMessage } from "@/lib/api";
import {
  attachmentPollInterval,
  attachmentState,
  attachmentsReading,
  sendableAttachments,
} from "@/lib/attachments";
import { COMPOSER_HINTS, enterSends, useCoarsePointer } from "@/lib/composer";
import { formatDate, formatTime } from "@/lib/format";
import { buildReview, withoutAttachment, type SendReview } from "@/lib/privacy";
import { spaceParam, useSpace } from "@/lib/space";
import type {
  AgentInfo,
  AgentName,
  Conversation,
  ConversationDetail,
  DocumentSummary,
  Message,
  Page,
  SendPreview,
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
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
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
  /** Every attachment shown when the message was sent (sent or left out): cleared once
   * the message is saved, unlike attachments added while it was being sent. */
  shown?: string[];
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
  const [uploading, setUploading] = useState(false);
  // The control before sending, when something is masked or left out (the message waits).
  const [review, setReview] = useState<{ vars: SendVars; review: SendReview } | null>(null);
  const list = useRef<HTMLOListElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const coarsePointer = useCoarsePointer();
  // On a phone the conversation list is folded away while a conversation is open.
  const [showList, setShowList] = useState(false);
  const listId = useId();

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
      refetchInterval: (query: { state: { data?: DocumentSummary; status: string } }) =>
        query.state.status === "error" ? false : attachmentPollInterval(attachmentState(query.state.data)),
    })),
  });
  const attachmentDocs = attachmentQueries.map((q) => q.data);
  // Gone: deleted meanwhile, or never loaded. Left out of the message, never waited for.
  const attachmentStates = attachmentQueries.map((q) =>
    attachmentState(q.data, q.isError && (!q.data || isApiError(q.error, "document_not_found"))),
  );
  const readingAttachments = attachmentsReading(attachmentStates);

  useEffect(() => {
    // The list scrolls itself to its last message (a sentinel element would be an invalid
    // child of the <ol>).
    const node = list.current;
    if (node) node.scrollTop = node.scrollHeight;
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
    setReview(null);
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
    onSuccess: ({ result, spaceId: space }, { content, resend, attachments = [], shown = [] }) => {
      if (!resend) {
        setDraft("");
        // Only those shown at sending time: one attached meanwhile is for the next message.
        setAttachmentIds((ids) => ids.filter((id) => !shown.includes(id)));
      }
      const missing = missingPermissions(result);
      if (missing !== null && result.agent !== "core") {
        // The message is saved; the agent first needs read access in this space.
        setDegraded(null);
        setPermission({
          agent: result.agent,
          spaceId: space,
          missing,
          content,
          attachments,
          messageId: result.user_message.id,
        });
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

  const [erasing, setErasing] = useState<string | null>(null);

  // Same rules as the sending, checked on the server: nothing is saved or sent.
  const check = useMutation({
    mutationFn: (vars: SendVars) =>
      api<SendPreview>("/v1/chat/preview", {
        method: "POST",
        body: {
          space_id: spaceId ?? writeSpaceId,
          content: vars.content,
          agent: vars.agent,
          attachments: vars.attachments ?? [],
        },
      }),
  });

  const submit = () => {
    const content = draft.trim();
    // Wait for a conversation being created: sending now would open a second one.
    if (content && !send.isPending && !check.isPending && !create.isPending && !readingAttachments && !uploading) {
      // As shown now: what the owner sees is what is checked, then sent.
      const shown = attachmentIds;
      const states = attachmentStates;
      const documents = attachmentDocs;
      const vars: SendVars = { content, agent, attachments: sendableAttachments(shown, states), shown };
      setReview(null);
      check.mutate(vars, {
        onSuccess: (preview) => {
          const result = buildReview(shown, states, documents, preview);
          const next = { ...vars, attachments: result.attachments };
          // No friction when nothing is masked or left out.
          if (result.needed) setReview({ vars: next, review: result });
          else send.mutate(next);
        },
      });
    }
  };

  const removeFromReview = (id: string) => {
    setAttachmentIds((ids) => ids.filter((other) => other !== id));
    setReview((current) =>
      current && {
        vars: {
          ...current.vars,
          attachments: (current.vars.attachments ?? []).filter((other) => other !== id),
          shown: (current.vars.shown ?? []).filter((other) => other !== id),
        },
        review: withoutAttachment(current.review, id),
      },
    );
  };

  return (
    <>
      <PageHeader title="Assistant">
        <Button variant="secondary" size="sm" onClick={() => create.mutate()} disabled={create.isPending}>
          Nouvelle conversation
        </Button>
      </PageHeader>
      {/* On a phone the list folds away above the conversation (master-detail, as in
          Objectifs and Documents): the composer stays within reach. */}
      <Button
        variant="secondary"
        size="sm"
        className="mb-3 md:hidden"
        aria-expanded={showList}
        aria-controls={listId}
        onClick={() => setShowList((v) => !v)}
      >
        {showList ? "Masquer les conversations" : "Afficher les conversations"}
      </Button>
      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <Card title="Conversations" className={cx("max-h-[70vh] overflow-y-auto md:block", !showList && "hidden")}>
          <div id={listId}>
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
                      setShowList(false);
                    }}
                    aria-current={c.id === current ? "true" : undefined}
                    className={cx(
                      "w-full rounded px-2 py-1.5 text-left text-sm",
                      c.id === current
                        ? "bg-teal-50 font-medium ring-1 ring-teal-200 dark:bg-teal-900/50 dark:ring-teal-800"
                        : "hover:bg-stone-100 dark:hover:bg-stone-800",
                    )}
                  >
                    <span className="line-clamp-1">{c.title}</span>
                    <span className="text-xs text-muted">
                      {formatDate(c.last_message_at ?? c.created_at)}
                      {!selected && ` · ${spaceName(c.space_id)}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          </div>
        </Card>

        <Card
          title={detail.data?.title ?? "Nouvelle conversation"}
          actions={
            current && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setErasing(current)}
                disabled={erasing === current}
                aria-label={`Supprimer la conversation « ${detail.data?.title ?? ""} »`}
              >
                Supprimer
              </Button>
            )
          }
          footer={`Espace : ${spaceName(detail.data?.space_id ?? writeSpaceId)} · conversations brutes conservées 90 jours sauf « Conserver »`}
        >
          <div className="flex min-h-[40vh] flex-col gap-3">
            {erasing && erasing === current && (
              <EraseConfirm
                question="Supprimer cette conversation et tous ses messages ?"
                confirmLabel="Supprimer la conversation"
                deletePath={`/v1/chat/conversations/${erasing}`}
                derivedPath={`/v1/chat/conversations/${erasing}/derived`}
                onDone={(withDerived) => {
                  setErasing(null);
                  openConversation(null, null);
                  void queryClient.invalidateQueries({ queryKey: ["conversations"] });
                  if (withDerived) {
                    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
                    void queryClient.invalidateQueries({ queryKey: ["memory"] });
                  }
                }}
                onCancel={() => setErasing(null)}
              />
            )}
            {current && detail.data?.id === current && detail.data.messages.length > 0 && (
              <ConsolidationPanel key={detail.data.id} conversation={detail.data} />
            )}
            <ol
              ref={list}
              aria-label="Messages"
              className="flex max-h-[50vh] flex-1 flex-col gap-3 overflow-y-auto pr-1"
              aria-live="polite"
            >
              {detail.data?.messages.map((m) => (
                <MessageBubble key={m.id} message={m} conversationId={detail.data.id} agents={agents.data} />
              ))}
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
                  send.mutate({
                    content: permission.content,
                    agent: permission.agent,
                    attachments: permission.attachments ?? [],
                    resend: true,
                  });
                }}
                onDismiss={() => setPermission(null)}
              />
            )}
            {send.error && <Alert tone="error">{errorMessage(send.error)}</Alert>}
            {check.error && (
              <Alert tone="error">
                Contrôle avant l&apos;envoi impossible, rien n&apos;a été envoyé : {errorMessage(check.error)}
              </Alert>
            )}
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
                states={attachmentStates}
                onUploadingChange={setUploading}
                onAdd={(id) => {
                  setReview(null);
                  setAttachmentIds((ids) => (ids.includes(id) ? ids : [...ids, id]));
                }}
                onRemove={(id) => {
                  setReview(null);
                  setAttachmentIds((ids) => ids.filter((other) => other !== id));
                }}
              />
              <VoiceRecorder
                spaceId={spaceId ?? writeSpaceId}
                onTranscript={(text) => {
                  setReview(null);
                  setDraft((current) => appendTranscript(current, text, { multiline: false }));
                }}
              />
              <Field label="Votre message" hint={coarsePointer ? COMPOSER_HINTS.coarse : COMPOSER_HINTS.fine}>
                <Textarea
                  ref={composer}
                  value={draft}
                  maxLength={8000}
                  onChange={(e) => {
                    // The control was for the message as it was: a changed message is checked again.
                    setReview(null);
                    setDraft(e.target.value);
                  }}
                  onKeyDown={(e) => {
                    const keys = { key: e.key, shiftKey: e.shiftKey, isComposing: e.nativeEvent.isComposing };
                    if (enterSends(keys, coarsePointer)) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
              </Field>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <AgentSelector
                  agents={agents.data}
                  value={agent}
                  onChange={(value) => {
                    setReview(null);
                    setAgent(value);
                  }}
                  spaceName={spaceName(spaceId ?? writeSpaceId)}
                  error={agents.error}
                />
                <Button
                  type="submit"
                  className="self-end sm:mt-6"
                  disabled={
                    send.isPending ||
                    check.isPending ||
                    create.isPending ||
                    readingAttachments ||
                    uploading ||
                    !draft.trim()
                  }
                >
                  {uploading
                    ? "Envoi de la pièce jointe…"
                    : readingAttachments
                      ? "Lecture de la pièce jointe…"
                      : check.isPending
                        ? "Vérification…"
                        : "Envoyer"}
                </Button>
              </div>
              {review && (
                <SendReviewPanel
                  review={review.review}
                  sending={send.isPending}
                  onConfirm={() => {
                    send.mutate(review.vars);
                    setReview(null);
                  }}
                  onCancel={() => {
                    // Back to the message, unchanged (WCAG 2.4.3: the focus is not lost).
                    setReview(null);
                    composer.current?.focus();
                  }}
                  onRemove={removeFromReview}
                />
              )}
            </form>
          </div>
        </Card>
      </div>
    </>
  );
}
