"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId } from "react";

import { Alert, Button, Spinner } from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import { AGENT_ACCESS_DAYS, agentsKey, resourceList } from "@/lib/agents";
import { loginWithPasskey } from "@/lib/passkeys";
import type { AgentInfo, AgentName } from "@/lib/types";

/** A reply refused with `agent_permission_required`: what to grant, and what to resend. */
export interface AgentPermissionNeed {
  agent: AgentName;
  spaceId: string;
  /** Resources the agent still cannot read in this space. */
  missing: string[];
  /** Content of the message to resend once access is granted. */
  content: string;
  /** Documents attached to that message, resent with it. */
  attachments?: string[];
}

export function AgentPermissionCard({
  need,
  label,
  spaceName,
  resending,
  onResend,
  onDismiss,
}: {
  need: AgentPermissionNeed;
  label: string;
  spaceName: string;
  resending: boolean;
  onResend: () => void;
  onDismiss: () => void;
}) {
  const queryClient = useQueryClient();
  const headingId = useId();
  const grant = useMutation({
    // Granting a permission to an agent is a reinforced operation (recent passkey).
    mutationFn: () =>
      withReauth(
        () =>
          api<AgentInfo[]>(`/v1/agents/${encodeURIComponent(need.agent)}/access`, {
            method: "PUT",
            body: { space_id: need.spaceId, enabled: true },
          }),
        loginWithPasskey,
      ),
    onSuccess: (agents) => {
      queryClient.setQueryData(agentsKey(need.spaceId), agents);
      void queryClient.invalidateQueries({ queryKey: ["permissions"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const stillMissing = grant.data?.find((a) => a.name === need.agent)?.missing_permissions ?? [];
  const resources = need.missing.length > 0 ? resourceList(need.missing) : "ses données";

  return (
    <div
      role="group"
      aria-labelledby={headingId}
      className="flex flex-col gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-50"
    >
      <h3 id={headingId} className="font-semibold">
        Autoriser « {label} » dans cet espace ?
      </h3>
      <p>
        Pour répondre, l&apos;agent {label} a besoin d&apos;un accès en lecture à ces données de l&apos;espace «{" "}
        {spaceName} » : <strong>{resources}</strong>, pendant {AGENT_ACCESS_DAYS} jours. Il ne modifie rien : ses
        propositions passent toujours par vos approbations.
      </p>
      <p className="text-xs text-amber-900/80 dark:text-amber-100/80">
        Une confirmation par passkey vous sera demandée. Vous pourrez retirer cet accès à tout moment dans les{" "}
        <Link href="/settings" className="underline">
          réglages
        </Link>
        . Le message est déjà enregistré dans la conversation.
      </p>

      {grant.isSuccess ? (
        stillMissing.length === 0 ? (
          <Alert tone="success">
            Accès accordé pour {AGENT_ACCESS_DAYS} jours. Vous pouvez renvoyer le message à l&apos;agent {label}.
          </Alert>
        ) : (
          <Alert tone="warning">Accès encore manquant : {resourceList(stillMissing)}.</Alert>
        )
      ) : null}
      {grant.error && <Alert tone="error">{errorMessage(grant.error)}</Alert>}
      {grant.isPending && <Spinner label="Confirmation de l'accès…" />}

      <div className="flex flex-wrap gap-2">
        {grant.isSuccess && stillMissing.length === 0 ? (
          <Button onClick={onResend} disabled={resending}>
            Renvoyer
          </Button>
        ) : (
          <Button onClick={() => grant.mutate()} disabled={grant.isPending}>
            Autoriser
          </Button>
        )}
        <Button variant="ghost" onClick={onDismiss}>
          Plus tard
        </Button>
      </div>
    </div>
  );
}
