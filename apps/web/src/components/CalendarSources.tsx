"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { EVENTS_KEY } from "@/components/AgendaWeek";
import { Alert, Badge, Button, Select, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { sourceErrorMessage } from "@/lib/calendar";
import { ACCOUNTS_KEY, grantsIn } from "@/lib/connectors";
import { formatDateTime } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { ConnectorAccount, ConnectorSource, RemoteCalendar } from "@/lib/types";

/**
 * Which calendars of an account feed which space (task 14). Only spaces where the owner
 * consented to calendar reading are offered; the server checks it again.
 */
export function CalendarSources({ account }: { account: ConnectorAccount }) {
  const queryClient = useQueryClient();
  const { spaces, spaceName } = useSpace();
  const readable = spaces.filter((s) => grantsIn(account, s.id).some((g) => g.capability === "calendar.read"));
  const [open, setOpen] = useState(false);
  const calendars = useQuery({
    queryKey: ["connector-calendars", account.id],
    queryFn: () => api<RemoteCalendar[]>(`/v1/connectors/accounts/${account.id}/calendars`),
    enabled: open,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["connector-calendars", account.id] });
    void queryClient.invalidateQueries({ queryKey: ACCOUNTS_KEY });
    void queryClient.invalidateQueries({ queryKey: [EVENTS_KEY] });
  };
  const follow = useMutation({
    mutationFn: (body: { provider_ref: string; space_id: string }) =>
      api<ConnectorSource>("/v1/connectors/sources", {
        method: "POST",
        body: { account_id: account.id, ...body },
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
  const sources = new Map(account.sources.filter((s) => s.kind === "calendar").map((s) => [s.id, s]));

  if (readable.length === 0 && sources.size === 0) return null;
  return (
    <div className="mt-2 flex flex-col gap-2">
      <h4 className="text-xs font-medium">Agendas suivis</h4>
      {[...sources.values()].length > 0 && (
        <ul className="flex flex-col gap-1 text-xs">
          {[...sources.values()].map((source) => {
            const problem = sourceErrorMessage(source.last_error);
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
                    if (window.confirm(`Ne plus suivre « ${source.name} » ? Ses événements copiés dans LifeOS seront supprimés.`))
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
            Choisir les agendas à suivre
          </Button>
        </div>
      )}
      {open && calendars.isPending && <Spinner label="Lecture des agendas du compte…" />}
      {open && calendars.error && <Alert tone="warning">{errorMessage(calendars.error)}</Alert>}
      {open && calendars.data && (
        <ul aria-label="Agendas du compte" className="flex flex-col gap-1 text-sm">
          {calendars.data
            .filter((c) => !c.source_id)
            .map((calendar) => (
              <FollowRow
                key={calendar.provider_ref}
                calendar={calendar}
                spaces={readable}
                pending={follow.isPending}
                onFollow={(spaceId) => follow.mutate({ provider_ref: calendar.provider_ref, space_id: spaceId })}
              />
            ))}
          {calendars.data.every((c) => c.source_id) && (
            <li className="text-xs text-stone-500">Tous les agendas de ce compte sont déjà suivis.</li>
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
  calendar,
  spaces,
  pending,
  onFollow,
}: {
  calendar: RemoteCalendar;
  spaces: { id: string; name: string }[];
  pending: boolean;
  onFollow: (spaceId: string) => void;
}) {
  const [spaceId, setSpaceId] = useState(spaces[0]?.id ?? "");
  return (
    <li className="flex flex-wrap items-center gap-2">
      <span>
        {calendar.name}
        {calendar.primary && " (principal)"}
        {calendar.read_only && <span className="text-xs text-stone-500"> · lecture seule</span>}
      </span>
      <Select
        aria-label={`Espace pour l'agenda ${calendar.name}`}
        value={spaceId}
        onChange={(e) => setSpaceId(e.target.value)}
      >
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
