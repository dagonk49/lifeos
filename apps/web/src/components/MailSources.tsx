"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Badge, Button, Select, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { ACCOUNTS_KEY, grantsIn } from "@/lib/connectors";
import { formatDateTime } from "@/lib/format";
import { MAIL_KEY, labelErrorMessage } from "@/lib/mail";
import { useSpace } from "@/lib/space";
import type { ConnectorAccount, ConnectorSource, RemoteLabel } from "@/lib/types";

/**
 * Which mail labels of an account feed which space (task 17). Only the labels chosen
 * here are copied, each into one space where the owner consented to mail reading.
 */
export function MailSources({ account }: { account: ConnectorAccount }) {
  const queryClient = useQueryClient();
  const { spaces, spaceName } = useSpace();
  const readable = spaces.filter((s) => grantsIn(account, s.id).some((g) => g.capability === "mail.read"));
  const [open, setOpen] = useState(false);
  const labels = useQuery({
    queryKey: ["connector-labels", account.id],
    queryFn: () => api<RemoteLabel[]>(`/v1/connectors/accounts/${account.id}/labels`),
    enabled: open,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["connector-labels", account.id] });
    void queryClient.invalidateQueries({ queryKey: ACCOUNTS_KEY });
    void queryClient.invalidateQueries({ queryKey: [MAIL_KEY] });
  };
  const follow = useMutation({
    mutationFn: (body: { provider_ref: string; space_id: string }) =>
      api<ConnectorSource>("/v1/connectors/sources", {
        method: "POST",
        body: { account_id: account.id, kind: "mail_label", ...body },
      }),
    onSuccess: refresh,
  });
  const unfollow = useMutation({
    mutationFn: (sourceId: string) => api(`/v1/connectors/sources/${sourceId}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const syncNow = useMutation({
    mutationFn: (sourceId: string) => api<ConnectorSource>(`/v1/connectors/sources/${sourceId}/sync`, { method: "POST" }),
    onSuccess: refresh,
  });
  const sources = account.sources.filter((s) => s.kind === "mail_label");

  if (readable.length === 0 && sources.length === 0) return null;
  return (
    <div className="mt-2 flex flex-col gap-2">
      <h4 className="text-xs font-medium">Libellés e-mail suivis</h4>
      {sources.length > 0 && (
        <ul className="flex flex-col gap-1 text-xs">
          {sources.map((source) => {
            const problem = labelErrorMessage(source.last_error);
            return (
              <li key={source.id} className="flex flex-wrap items-center gap-2">
                <span>
                  « {source.name} » → {spaceName(source.space_id)} ·{" "}
                  {source.last_synced_at ? `lu le ${formatDateTime(source.last_synced_at)}` : "pas encore lu"}
                </span>
                {problem && <Badge tone="amber">{problem}</Badge>}
                <Button size="sm" variant="ghost" onClick={() => syncNow.mutate(source.id)} disabled={syncNow.isPending}>
                  Synchroniser
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Ne plus suivre « ${source.name} » ? Ses e-mails copiés dans LifeOS seront supprimés (les pièces jointes importées dans Documents restent).`,
                      )
                    )
                      unfollow.mutate(source.id);
                  }}
                  disabled={unfollow.isPending}
                >
                  Ne plus suivre
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {syncNow.isSuccess && <p className="text-xs text-stone-600 dark:text-stone-400">Synchronisation demandée.</p>}
      {readable.length > 0 && account.status === "active" && !open && (
        <div>
          <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
            Choisir les libellés à suivre
          </Button>
        </div>
      )}
      {open && (
        <p className="text-xs text-stone-600 dark:text-stone-400">
          Seuls les libellés choisis sont copiés (90 derniers jours, texte seul). L'accès accordé chez le fournisseur
          couvre toute la boîte : c'est ce choix qui limite ce que LifeOS lit.
        </p>
      )}
      {open && labels.isPending && <Spinner label="Lecture des libellés du compte…" />}
      {open && labels.error && <Alert tone="warning">{errorMessage(labels.error)}</Alert>}
      {open && labels.data && (
        <ul aria-label="Libellés du compte" className="flex flex-col gap-1 text-sm">
          {labels.data
            .filter((label) => !label.source_id)
            .map((label) => (
              <FollowRow
                key={label.provider_ref}
                label={label}
                spaces={readable}
                pending={follow.isPending}
                onFollow={(spaceId) => follow.mutate({ provider_ref: label.provider_ref, space_id: spaceId })}
              />
            ))}
          {labels.data.every((label) => label.source_id) && (
            <li className="text-xs text-stone-500">Tous les libellés de ce compte sont déjà suivis.</li>
          )}
        </ul>
      )}
      {(follow.error || unfollow.error || syncNow.error) && (
        <Alert tone="error">{errorMessage(follow.error ?? unfollow.error ?? syncNow.error)}</Alert>
      )}
    </div>
  );
}

function FollowRow({
  label,
  spaces,
  pending,
  onFollow,
}: {
  label: RemoteLabel;
  spaces: { id: string; name: string }[];
  pending: boolean;
  onFollow: (spaceId: string) => void;
}) {
  const [spaceId, setSpaceId] = useState(spaces[0]?.id ?? "");
  return (
    <li className="flex flex-wrap items-center gap-2">
      <span>{label.name}</span>
      <Select aria-label={`Espace pour le libellé ${label.name}`} value={spaceId} onChange={(e) => setSpaceId(e.target.value)}>
        {spaces.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </Select>
      <Button size="sm" onClick={() => onFollow(spaceId)} disabled={pending || !spaceId}>
        Suivre dans cet espace
      </Button>
    </li>
  );
}
