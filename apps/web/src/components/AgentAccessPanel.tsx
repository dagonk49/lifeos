"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { UsageSwitch } from "@/components/UsageSwitch";
import { Alert, Badge, Card, Empty, Spinner } from "@/components/ui";
import { AGENT_ACCESS_DAYS, agentsKey, resourceList } from "@/lib/agents";
import { api, errorMessage, withReauth } from "@/lib/api";
import { loginWithPasskey } from "@/lib/passkeys";
import type { AgentInfo, AgentPermission, Space } from "@/lib/types";
import {
  ACCESS_STATUS_LABELS,
  ACCESS_STATUS_TONES,
  accessExpiryLabel,
  accessStillOpen,
  agentAccessExpiry,
  agentAccessStatus,
  agentResourcesLabel,
} from "@/lib/usage";

function AgentAccessRow({
  agent,
  spaceId,
  spaceName,
  permissions,
}: {
  agent: AgentInfo;
  spaceId: string;
  spaceName: string;
  permissions: AgentPermission[] | undefined;
}) {
  const queryClient = useQueryClient();
  const nameId = useId();
  const detailsId = useId();
  const [stillOpen, setStillOpen] = useState(false);
  const toggle = useMutation({
    // Granting is a reinforced operation (recent passkey); withdrawing is immediate.
    mutationFn: (enabled: boolean) =>
      withReauth(
        () =>
          api<AgentInfo[]>(`/v1/agents/${encodeURIComponent(agent.name)}/access`, {
            method: "PUT",
            body: { space_id: spaceId, enabled },
          }),
        loginWithPasskey,
      ),
    onMutate: () => setStillOpen(false),
    onSuccess: (agents, enabled) => {
      queryClient.setQueryData(agentsKey(spaceId), agents);
      setStillOpen(!enabled && accessStillOpen(agents, agent.name));
      void queryClient.invalidateQueries({ queryKey: ["permissions"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const status = agentAccessStatus(agent);
  const granted = status === "granted";
  const expiry =
    status === "none" || !permissions
      ? ""
      : accessExpiryLabel(agentAccessExpiry(permissions, agent.name, spaceId, agent.resources));
  const label = agent.label || agent.name;

  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span id={nameId} className="font-medium">
              {label}
            </span>
            <Badge tone={ACCESS_STATUS_TONES[status]}>{ACCESS_STATUS_LABELS[status]}</Badge>
          </p>
          <div id={detailsId} className="mt-0.5 text-xs text-stone-600 dark:text-stone-400">
            <p>{agent.description}</p>
            <p>
              {agentResourcesLabel(agent.resources)}
              {granted && expiry && ` · ${expiry}`}
            </p>
          </div>
        </div>
        <UsageSwitch
          checked={granted}
          label={`Accès de l'agent ${label} à l'espace ${spaceName}`}
          describedBy={detailsId}
          disabled={toggle.isPending}
          onChange={(next) => toggle.mutate(next)}
        />
      </div>
      {toggle.isPending && <Spinner label={granted ? "Retrait de l'accès…" : "Confirmation de l'accès…"} />}
      {status === "partial" && !toggle.isPending && (
        <p className="text-xs text-amber-800 dark:text-amber-200">
          Il lui manque l&apos;accès à : {resourceList(agent.missing_permissions)}. Activez l&apos;accès pour le
          compléter.
        </p>
      )}
      {stillOpen && (
        <Alert tone="info">
          Accès accordé par cette page retiré, mais d&apos;autres permissions restent actives (bilans automatiques
          ou permission manuelle) : retirez-les dans « Permissions des agents ».
        </Alert>
      )}
      {toggle.error && <Alert tone="error">{errorMessage(toggle.error)}</Alert>}
    </li>
  );
}

function SpaceAgents({
  space,
  permissions,
}: {
  space: Space;
  permissions: AgentPermission[] | undefined;
}) {
  const headingId = useId();
  const agents = useQuery({
    queryKey: agentsKey(space.id),
    queryFn: () => api<AgentInfo[]>(`/v1/agents?space_id=${encodeURIComponent(space.id)}`),
  });

  return (
    <section aria-labelledby={headingId} className="flex flex-col">
      <h3 id={headingId} className="flex flex-wrap items-center gap-2 text-sm font-semibold">
        Espace « {space.name} »
        {space.cloud_ai_enabled === false && <Badge tone="amber">Assistant désactivé ici</Badge>}
      </h3>
      {space.cloud_ai_enabled === false && (
        <p className="mt-1 text-xs text-stone-500">
          Les agents utilisent l&apos;assistant IA : activez-le pour cet espace (section « Espaces et assistant IA »)
          pour qu&apos;ils puissent répondre.
        </p>
      )}
      {agents.isPending ? (
        <Spinner />
      ) : agents.error ? (
        <Alert tone="error">{errorMessage(agents.error)}</Alert>
      ) : agents.data.length === 0 ? (
        <Empty>Aucun agent spécialisé disponible.</Empty>
      ) : (
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {agents.data.map((agent) => (
            <AgentAccessRow
              key={agent.name}
              agent={agent}
              spaceId={space.id}
              spaceName={space.name}
              permissions={permissions}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Settings card: on-demand specialised agents and their read access, per space. */
export function AgentAccessPanel({ className }: { className?: string }) {
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: () => api<Space[]>("/v1/spaces") });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: () => api<AgentPermission[]>("/v1/guardian/permissions"),
  });

  return (
    <Card title="Agents spécialisés" className={className}>
      <div className="flex flex-col gap-4 text-sm">
        <p className="text-stone-600 dark:text-stone-400">
          Un agent n&apos;est utilisé que si vous le choisissez dans l&apos;assistant. Son accès est en{" "}
          <strong>lecture seule</strong>, limité aux données dont il a besoin dans l&apos;espace, accordé pour{" "}
          {AGENT_ACCESS_DAYS} jours et révocable à tout moment. Il ne modifie rien : ses propositions passent par vos
          approbations. Accorder un accès demande une confirmation par passkey ; le retirer est immédiat.
        </p>
        {spaces.isPending ? (
          <Spinner />
        ) : spaces.error ? (
          <Alert tone="error">{errorMessage(spaces.error)}</Alert>
        ) : (
          spaces.data.map((space) => <SpaceAgents key={space.id} space={space} permissions={permissions.data} />)
        )}
      </div>
    </Card>
  );
}
