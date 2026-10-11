"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useState } from "react";

import { ApprovalReview } from "@/components/ApprovalReview";
import { Alert, Button, Spinner } from "@/components/ui";
import { api, errorMessage, isApiError } from "@/lib/api";
import { NOTHING_DURABLE, consolidationSummary, newMessagesLabel, newOwnerMessages } from "@/lib/agents";
import { formatDateTime } from "@/lib/format";
import type { Approval, ConsolidationResult, ConversationDetail } from "@/lib/types";

const HELP =
  "Seuls vos propres messages sont analysés (jamais les réponses de l'assistant), en une seule requête Gemini. " +
  "Rien n'est enregistré avant votre approbation.";

/**
 * "Retenir l'essentiel": proposes the durable facts and tasks of the owner's new messages
 * (raw messages expire after 90 days), for review before anything is saved.
 */
export function ConsolidationPanel({ conversation }: { conversation: ConversationDetail }) {
  const queryClient = useQueryClient();
  const helpId = useId();
  const [outcome, setOutcome] = useState<ConsolidationResult | null>(null);
  const pending = newOwnerMessages(conversation.messages, conversation.consolidated_until);

  const refreshConversation = () => {
    void queryClient.invalidateQueries({ queryKey: ["conversation", conversation.id] });
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
  };
  const consolidate = useMutation({
    mutationFn: () =>
      api<ConsolidationResult>(`/v1/chat/conversations/${conversation.id}/consolidate`, { method: "POST" }),
    onMutate: () => setOutcome(null),
    onSuccess: (result) => {
      setOutcome(result);
      refreshConversation();
      if (result.approval_id) {
        void queryClient.invalidateQueries({ queryKey: ["approvals"] });
        void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      }
    },
  });
  const approvalId = outcome?.approval_id ?? null;
  const approval = useQuery({
    // Under ["approvals"]: refreshed when ApprovalReview approves or rejects.
    queryKey: ["approvals", "detail", approvalId],
    queryFn: () => api<Approval>(`/v1/approvals/${approvalId}`),
    enabled: approvalId !== null,
  });
  const awaitingDecision = approval.data?.status === "pending";
  const nothingNew = isApiError(consolidate.error, "nothing_to_consolidate");

  return (
    <section
      aria-label="Consolidation de la conversation"
      className="flex flex-col gap-2 rounded-md border border-stone-200 bg-stone-50 p-2.5 text-sm dark:border-stone-800 dark:bg-stone-950/40"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-stone-600 dark:text-stone-400">
          {conversation.consolidated_until
            ? `Consolidée jusqu'au ${formatDateTime(conversation.consolidated_until)}`
            : "Pas encore consolidée"}{" "}
          · {newMessagesLabel(pending)}
        </p>
        <Button
          variant="secondary"
          size="sm"
          title={HELP}
          aria-describedby={helpId}
          disabled={consolidate.isPending || pending === 0 || awaitingDecision}
          onClick={() => consolidate.mutate()}
        >
          Retenir l&apos;essentiel
        </Button>
      </div>
      <p id={helpId} className="text-xs text-stone-500">
        {HELP}
        {awaitingDecision && " Validez ou refusez d'abord les propositions ci-dessous."}
      </p>

      {consolidate.isPending && <Spinner label="Analyse de vos messages…" />}
      {consolidate.error &&
        (nothingNew ? (
          <Alert tone="info">{errorMessage(consolidate.error)}</Alert>
        ) : (
          <Alert tone="error">{errorMessage(consolidate.error)}</Alert>
        ))}

      {outcome &&
        (outcome.approval_id === null ? (
          <Alert tone="info">{NOTHING_DURABLE}</Alert>
        ) : (
          <div className="flex flex-col gap-2">
            <p>{consolidationSummary(outcome)}</p>
            {approval.isPending ? (
              <Spinner label="Chargement des propositions…" />
            ) : approval.error ? (
              <Alert tone="error">{errorMessage(approval.error)}</Alert>
            ) : (
              <ApprovalReview
                key={`${approval.data.id}-${approval.data.status}`}
                approval={approval.data}
                onDone={refreshConversation}
              />
            )}
            <p className="text-xs">
              <Link href="/approvals" className="text-teal-800 underline dark:text-teal-300">
                Voir toutes les approbations
              </Link>
            </p>
          </div>
        ))}
    </section>
  );
}
