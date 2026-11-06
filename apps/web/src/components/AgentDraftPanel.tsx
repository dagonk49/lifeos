"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useState } from "react";

import { DRAFTS_KEY } from "@/components/MailCompose";
import { Alert, Button, Spinner } from "@/components/ui";
import { agentLabel, CORE_AGENT, type AgentChoice } from "@/lib/agents";
import { api, errorMessage, isApiError } from "@/lib/api";
import { AGENT_DRAFT_NOTICE, draftAuthorLabel, droppedRecipientsLabel, subjectLabel } from "@/lib/mail";
import type { AgentDraftResult, AgentInfo, MailDraft } from "@/lib/types";

function Recipients({ label, list }: { label: string; list: string[] }) {
  return (
    <div className="flex min-w-0 flex-wrap gap-x-2">
      <dt className="font-medium">{label}</dt>
      <dd className="min-w-0 break-all">{list.join(", ")}</dd>
    </div>
  );
}

/** A draft an agent wrote, as data: plain text only (T14), every recipient shown. */
export function AgentDraftCard({
  draft,
  dropped = 0,
  agents,
}: {
  draft: MailDraft;
  dropped?: number;
  agents?: readonly AgentInfo[];
}) {
  const droppedNote = droppedRecipientsLabel(dropped);
  return (
    <article
      aria-label={draftAuthorLabel(draft, agents) ?? "Brouillon"}
      className="flex min-w-0 flex-col gap-2 rounded-md border border-stone-200 bg-white p-3 dark:border-stone-800 dark:bg-stone-900"
    >
      <p className="font-medium">{draftAuthorLabel(draft, agents)}</p>
      <Alert tone="info">{AGENT_DRAFT_NOTICE}</Alert>
      {droppedNote && <Alert tone="warning">{droppedNote}</Alert>}
      <dl className="flex flex-col gap-1 text-sm">
        {draft.to.length > 0 ? (
          <Recipients label="À" list={draft.to} />
        ) : (
          <div className="flex flex-wrap gap-x-2">
            <dt className="font-medium">À</dt>
            <dd className="text-muted">aucun destinataire : à compléter</dd>
          </div>
        )}
        {draft.cc.length > 0 && <Recipients label="Cc" list={draft.cc} />}
        <div className="flex min-w-0 flex-wrap gap-x-2">
          <dt className="font-medium">Objet</dt>
          <dd className="min-w-0 break-words">{subjectLabel(draft.subject)}</dd>
        </div>
      </dl>
      {/* Written by a model: plain text, never HTML nor Markdown (threat model T14). */}
      <p className="max-h-64 overflow-y-auto rounded bg-stone-100 p-2 text-sm whitespace-pre-wrap break-words dark:bg-stone-800">
        {draft.body}
      </p>
      <p className="text-sm">
        <Link
          href={`/mail/compose?draft=${encodeURIComponent(draft.id)}`}
          className="text-teal-800 underline dark:text-teal-300"
        >
          Vérifier et préparer l&apos;envoi
        </Link>
      </p>
    </article>
  );
}

/**
 * « Proposer un brouillon d'e-mail »: the chosen agent of the conversation writes a draft
 * (one AI request). Nothing is sent: the owner checks it on the compose page, prepares the
 * sending and approves the exact message.
 */
export function AgentDraftPanel({
  conversationId,
  agent = CORE_AGENT,
  agents,
  replyToId = null,
  label = "Proposer un brouillon d'e-mail",
}: {
  conversationId: string;
  agent?: AgentChoice;
  agents?: readonly AgentInfo[];
  /** The e-mail answered (its sender becomes the recipient). */
  replyToId?: string | null;
  label?: string;
}) {
  const queryClient = useQueryClient();
  const helpId = useId();
  const [result, setResult] = useState<AgentDraftResult | null>(null);
  const propose = useMutation({
    mutationFn: (draftId: string | null) =>
      api<AgentDraftResult>(`/v1/chat/conversations/${conversationId}/mail-draft`, {
        method: "POST",
        idempotent: true,
        body: draftId
          ? { agent: result?.draft.author_agent ?? agent, draft_id: draftId }
          : { agent, in_reply_to_id: replyToId },
      }),
    onSuccess: (created) => {
      setResult(created);
      queryClient.setQueryData([DRAFTS_KEY, "detail", created.draft.id], created.draft);
      void queryClient.invalidateQueries({ queryKey: [DRAFTS_KEY, "list"] });
      void queryClient.invalidateQueries({ queryKey: ["llm-usage"] });
    },
  });
  const writer = agentLabel(agent, agents);
  return (
    <section
      aria-label="Brouillon d'e-mail"
      className="flex flex-col gap-2 rounded-md border border-stone-200 bg-stone-50 p-2.5 text-sm dark:border-stone-800 dark:bg-stone-950/40"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p id={helpId} className="text-xs text-muted">
          {`Rédigé par l'agent ${writer} à partir de cette conversation, en une requête à l'IA. Rien n'est envoyé : vous vérifiez, puis vous préparez l'envoi.`}
        </p>
        <Button
          variant="secondary"
          size="sm"
          aria-describedby={helpId}
          disabled={propose.isPending}
          onClick={() => propose.mutate(null)}
        >
          {label}
        </Button>
      </div>
      {propose.isPending && <Spinner label="Rédaction du brouillon…" />}
      {propose.error &&
        (isApiError(propose.error, "agent_permission_required") ? (
          <Alert tone="warning">
            {`L'agent ${writer} n'a pas la permission « email » en écriture dans cet espace : accordez-la dans `}
            <Link href="/settings" className="underline">
              Réglages &gt; Permissions des agents
            </Link>
            {" "}pour qu&apos;il rédige des brouillons (il ne pourra jamais les envoyer).
          </Alert>
        ) : (
          <Alert tone="error">{errorMessage(propose.error)}</Alert>
        ))}
      {result && (
        <>
          <AgentDraftCard draft={result.draft} dropped={result.dropped_recipients} agents={agents} />
          {!result.draft.owner_edited && result.draft.state === "draft" && (
            <div>
              <Button
                variant="ghost"
                size="sm"
                disabled={propose.isPending}
                onClick={() => propose.mutate(result.draft.id)}
              >
                Proposer une autre version
              </Button>
              <span className="text-xs text-muted">
                {" "}
                Tenez compte de vos derniers messages ; possible tant que vous ne l&apos;avez pas modifié.
              </span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
