"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { CALENDAR_ACTIONS, CalendarApprovalPreview } from "@/components/CalendarApprovalPreview";
import { EMAIL_ACTIONS, EmailApprovalPreview } from "@/components/EmailApprovalPreview";
import { Alert, Badge, Button } from "@/components/ui";
import { api, errorMessage, problemMessage, withReauth } from "@/lib/api";
import { formatDate, formatDateTime, KIND_LABELS } from "@/lib/format";
import { loginWithPasskey } from "@/lib/passkeys";
import type { Approval, CalendarPreview, EmailPreview } from "@/lib/types";

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

// An e-mail: what the owner wants to know is whether it left.
const EMAIL_STATUS: Partial<Record<Approval["status"], string>> = {
  approved: "Envoi en cours",
  executed: "Envoyé",
  invalidated: "Invalidée",
};

function statusLabel(approval: Approval, isEmail: boolean): string {
  if (isEmail && approval.status === "failed" && approval.error === "outcome_unknown") return "État incertain";
  return (isEmail ? EMAIL_STATUS[approval.status] : undefined) ?? STATUS[approval.status];
}

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
  const isEmail = EMAIL_ACTIONS.has(approval.action);
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
    void queryClient.invalidateQueries({ queryKey: ["mail-drafts"] });
    onDone?.();
  };
  // Set on the first click, before React re-renders the disabled button: a double click
  // never sends two approvals (the server would refuse the second anyway).
  const decided = useRef(false);
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
    onError: () => {
      decided.current = false;
    },
  });
  const approveOnce = () => {
    if (decided.current) return;
    decided.current = true;
    approve.mutate();
  };
  const reject = useMutation({
    mutationFn: () => api<Approval>(`/v1/approvals/${approval.id}/reject`, { method: "POST" }),
    onSuccess: refresh,
  });
  const pending = approval.status === "pending";
  // One decision at a time, and only one: no double click, no approve-and-reject, no
  // second approval while the list refreshes.
  const busy = approve.isPending || reject.isPending || approve.isSuccess || reject.isSuccess;

  return (
    <article className="flex flex-col gap-3 rounded-md border border-stone-200 p-3 dark:border-stone-800">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-medium">{approval.description}</h3>
          <p className="text-xs text-muted">
            Demandée par {approval.requested_by} le {formatDateTime(approval.created_at)}
            {pending && ` · expire le ${formatDateTime(approval.expires_at)}`}
          </p>
        </div>
        <Badge tone={pending ? "amber" : approval.status === "executed" ? "teal" : "neutral"}>
          {statusLabel(approval, isEmail)}
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
                    {t.due_date && <span className="text-muted"> · échéance {formatDate(t.due_date)}</span>}
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
            <p className="text-muted">L&apos;assistant n&apos;a rien trouvé à proposer.</p>
          )}
        </div>
      ) : isCalendar ? (
        <CalendarApprovalPreview preview={approval.preview as unknown as CalendarPreview} />
      ) : isEmail ? (
        <EmailApprovalPreview preview={approval.preview as unknown as EmailPreview} />
      ) : (
        <pre
          role="region"
          aria-label="Détail de la demande"
          tabIndex={0}
          className="overflow-x-auto rounded bg-stone-100 p-2 text-xs dark:bg-stone-800"
        >
          {JSON.stringify(approval.preview, null, 2)}
        </pre>
      )}

      {isEmail && approval.status === "executed" && <Alert tone="success">Envoyé.</Alert>}
      {isEmail && approval.error === "outcome_unknown" ? (
        <Alert tone="warning">
          État incertain — vérifiez vos e-mails envoyés. LifeOS vérifie de son côté et ne renvoie rien
          automatiquement ; vous pouvez aussi l&apos;indiquer sur le brouillon.
        </Alert>
      ) : approval.error && (
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
          <Button onClick={approveOnce} disabled={busy}>
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
