"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { CALENDAR_ACTIONS, CalendarApprovalPreview } from "@/components/CalendarApprovalPreview";
import { Alert, Badge, Button } from "@/components/ui";
import { api, errorMessage, problemMessage, withReauth } from "@/lib/api";
import { formatDate, formatDateTime, KIND_LABELS } from "@/lib/format";
import { loginWithPasskey } from "@/lib/passkeys";
import type { Approval, CalendarPreview } from "@/lib/types";

interface ProposalParams {
  entry_id: string;
  tasks: { title: string; due_date: string | null }[];
  facts: { kind: string; statement: string; occurred_on: string | null }[];
}

const STATUS: Record<Approval["status"], string> = {
  pending: "En attente",
  approved: "Approuvée",
  rejected: "Refusée",
  expired: "Expirée",
  executed: "Exécutée",
  failed: "Échec",
  invalidated: "Plus valable",
};

/**
 * Shows exactly what will be executed. For journal proposals, the owner can untick items:
 * the approved parameters are then the edited list, nothing else.
 */
const PROPOSAL_ACTIONS = new Set(["journal.apply_proposals", "chat.apply_proposals"]);

export function ApprovalReview({ approval, onDone }: { approval: Approval; onDone?: () => void }) {
  const queryClient = useQueryClient();
  // Tasks and facts proposed by the assistant (journal analysis, conversation
  // consolidation): the owner keeps only what is right.
  const isProposals = PROPOSAL_ACTIONS.has(approval.action);
  const isCalendar = CALENDAR_ACTIONS.has(approval.action);
  // Reinforced (a shared event...): a recent passkey login is asked for, then it retries once.
  const reinforced = approval.level === "reinforced";
  const params = approval.params as unknown as ProposalParams;
  const [keepTasks, setKeepTasks] = useState<boolean[]>(() => (isProposals ? params.tasks.map(() => true) : []));
  const [keepFacts, setKeepFacts] = useState<boolean[]>(() => (isProposals ? params.facts.map(() => true) : []));

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    void queryClient.invalidateQueries({ queryKey: ["facts"] });
    void queryClient.invalidateQueries({ queryKey: ["journal"] });
    void queryClient.invalidateQueries({ queryKey: ["calendar-events"] });
    onDone?.();
  };
  const approve = useMutation({
    mutationFn: () => {
      const send = () =>
        api<Approval>(`/v1/approvals/${approval.id}/approve`, {
          method: "POST",
          body: isProposals
            ? {
                params: {
                  ...params,
                  tasks: params.tasks.filter((_, i) => keepTasks[i]),
                  facts: params.facts.filter((_, i) => keepFacts[i]),
                },
              }
            : {},
        });
      return reinforced ? withReauth(send, loginWithPasskey) : send();
    },
    onSuccess: refresh,
  });
  const reject = useMutation({
    mutationFn: () => api<Approval>(`/v1/approvals/${approval.id}/reject`, { method: "POST" }),
    onSuccess: refresh,
  });
  const pending = approval.status === "pending";
  // One decision at a time: no double click, no approve-and-reject.
  const busy = approve.isPending || reject.isPending;

  return (
    <article className="flex flex-col gap-3 rounded-md border border-stone-200 p-3 dark:border-stone-800">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-medium">{approval.description}</h3>
          <p className="text-xs text-stone-500">
            Demandée par {approval.requested_by} le {formatDateTime(approval.created_at)}
            {pending && ` · expire le ${formatDateTime(approval.expires_at)}`}
          </p>
        </div>
        <Badge tone={pending ? "amber" : approval.status === "executed" ? "teal" : "neutral"}>
          {STATUS[approval.status]}
        </Badge>
      </header>

      {isProposals ? (
        <div className="flex flex-col gap-2 text-sm">
          <p className="text-stone-600 dark:text-stone-400">
            Propositions de l&apos;assistant. Décochez ce qui est inexact : seuls les éléments cochés
            seront enregistrés.
          </p>
          {params.tasks.length > 0 && (
            <fieldset>
              <legend className="font-medium">Tâches</legend>
              {params.tasks.map((t, i) => (
                <label key={i} className="flex items-start gap-2 py-0.5">
                  <input
                    type="checkbox"
                    className="mt-1 accent-teal-700"
                    disabled={!pending}
                    checked={keepTasks[i] ?? false}
                    onChange={(e) => setKeepTasks((k) => k.map((v, j) => (j === i ? e.target.checked : v)))}
                  />
                  <span>
                    {t.title}
                    {t.due_date && <span className="text-stone-500"> · échéance {formatDate(t.due_date)}</span>}
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {params.facts.length > 0 && (
            <fieldset>
              <legend className="font-medium">Faits à mémoriser</legend>
              {params.facts.map((f, i) => (
                <label key={i} className="flex items-start gap-2 py-0.5">
                  <input
                    type="checkbox"
                    className="mt-1 accent-teal-700"
                    disabled={!pending}
                    checked={keepFacts[i] ?? false}
                    onChange={(e) => setKeepFacts((k) => k.map((v, j) => (j === i ? e.target.checked : v)))}
                  />
                  <span>
                    <Badge tone="violet">{KIND_LABELS[f.kind] ?? f.kind}</Badge> {f.statement}
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {params.tasks.length + params.facts.length === 0 && (
            <p className="text-stone-500">L&apos;assistant n&apos;a rien trouvé à proposer.</p>
          )}
        </div>
      ) : isCalendar ? (
        <CalendarApprovalPreview preview={approval.preview as unknown as CalendarPreview} />
      ) : (
        <pre className="overflow-x-auto rounded bg-stone-100 p-2 text-xs dark:bg-stone-800">
          {JSON.stringify(approval.preview, null, 2)}
        </pre>
      )}

      {approval.error && (
        <Alert
          tone={approval.status === "invalidated" || approval.error === "outcome_unknown" ? "warning" : "error"}
        >
          {approval.status === "invalidated" ? "Plus valable : " : "Échec de l’exécution : "}
          {problemMessage(approval.error, approval.error)}
        </Alert>
      )}
      {(approve.error || reject.error) && (
        <Alert tone="error">{errorMessage(approve.error ?? reject.error)}</Alert>
      )}
      {pending && (
        <div className="flex gap-2">
          <Button onClick={() => approve.mutate()} disabled={busy}>
            {reinforced ? "Approuver avec ma passkey" : "Approuver"}
          </Button>
          <Button variant="secondary" onClick={() => reject.mutate()} disabled={busy}>
            Refuser
          </Button>
        </div>
      )}
    </article>
  );
}
