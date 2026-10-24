"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Badge, Button, Spinner } from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { loginWithPasskey } from "@/lib/passkeys";
import { otherSessions, revokedOthersMessage } from "@/lib/sessions";
import type { RevokedSessions, SessionInfo } from "@/lib/types";

/**
 * Open sessions, one per device or browser. A session can be revoked on its own, or all
 * the others at once (a lost phone, a session the owner does not recognise): that one is
 * a reinforced operation, confirmed with a passkey when the login is not recent.
 */
export function SessionList() {
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionInfo[]>("/v1/auth/sessions") });
  const [notice, setNotice] = useState<string | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["sessions"] });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/v1/auth/sessions/${id}`, { method: "DELETE" }),
    onMutate: () => {
      setNotice(null);
      revokeOthers.reset();
    },
    onSuccess: refresh,
  });
  const revokeOthers = useMutation({
    mutationFn: () =>
      withReauth(
        () => api<RevokedSessions>("/v1/auth/sessions/revoke-others", { method: "POST" }),
        loginWithPasskey,
      ),
    onMutate: () => {
      setNotice(null);
      revoke.reset();
    },
    onSuccess: (result) => {
      setNotice(revokedOthersMessage(result.revoked));
      refresh();
    },
  });
  const others = otherSessions(sessions.data);
  const error = revoke.error ?? revokeOthers.error;

  return (
    <>
      <h3 className="mt-4 text-sm font-medium">Sessions ouvertes</h3>
      {sessions.isPending ? (
        <Spinner />
      ) : (
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {sessions.data?.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2 py-2">
              <span className="min-w-0">
                <span className="line-clamp-1">{s.user_agent ?? "Appareil inconnu"}</span>
                <span className="block text-xs text-stone-500">
                  Dernière activité {formatDateTime(s.last_seen_at)} {s.ip && `· ${s.ip}`}
                </span>
              </span>
              {s.current ? (
                <Badge tone="teal">Cette session</Badge>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Révoquer la session ${s.user_agent ?? "de l'appareil inconnu"}`}
                  onClick={() => revoke.mutate(s.id)}
                  disabled={revoke.isPending}
                >
                  Révoquer
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {others.length > 0 && (
        <div className="mt-2 flex flex-col items-start gap-1">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => revokeOthers.mutate()}
            disabled={revokeOthers.isPending}
          >
            Déconnecter les autres sessions
          </Button>
          <span className="text-xs text-stone-500">
            Un appareil perdu, une session inconnue : seule cette session reste ouverte. Confirmation
            par passkey si votre connexion date de plus de 10 minutes.
          </span>
        </div>
      )}
      {notice && <Alert tone="success">{notice}</Alert>}
      {error && <Alert tone="error">{errorMessage(error)}</Alert>}
    </>
  );
}
