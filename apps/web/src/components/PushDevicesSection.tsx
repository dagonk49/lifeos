"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, Empty, Spinner } from "@/components/ui";
import { api, errorMessage, isApiError, withReauth } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { loginWithPasskey } from "@/lib/passkeys";
import {
  currentSubscription,
  disableOnThisDevice,
  enableOnThisDevice,
  isPushSupported,
  needsHomeScreen,
} from "@/lib/push";
import type { PushConfig, PushDevice, PushPreview } from "@/lib/types";

const DEVICES = ["push", "devices"];

/**
 * Réglages → « Notifications sur le téléphone » (ADR 0019): enable Web Push on this
 * browser, see and revoke the devices that receive it, choose what their lock screen
 * shows, and send a test.
 */
export function PushDevicesSection({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const config = useQuery({
    queryKey: ["push", "config"],
    queryFn: () => api<PushConfig>("/v1/notifications/push/config"),
    retry: false,
  });
  const enabledOnServer = config.isSuccess;
  const devices = useQuery({
    queryKey: DEVICES,
    queryFn: () => api<PushDevice[]>("/v1/notifications/devices"),
    enabled: enabledOnServer,
  });
  const [supported, setSupported] = useState<boolean | null>(null);
  const [homeScreen, setHomeScreen] = useState(false);
  const [subscribed, setSubscribed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    // Browser features are only known on the client (the page is rendered on the server).
    const ok = isPushSupported();
    setSupported(ok);
    setHomeScreen(needsHomeScreen());
    if (ok) {
      void currentSubscription()
        .then((s) => setSubscribed(s !== null))
        .catch(() => setSubscribed(false));
    }
  }, []);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: DEVICES });
  const reset = () => {
    setNotice(null);
    for (const m of [enable, disable, revoke, test, preview]) m.reset();
  };
  const enable = useMutation({
    mutationFn: enableOnThisDevice,
    onMutate: () => reset(),
    onSuccess: () => {
      setSubscribed(true);
      setNotice("Notifications activées sur cet appareil.");
      refresh();
    },
  });
  const disable = useMutation({
    mutationFn: (deviceId: string | null) => disableOnThisDevice(deviceId),
    onMutate: () => reset(),
    onSuccess: () => {
      setSubscribed(false);
      setNotice("Notifications désactivées sur cet appareil.");
      refresh();
    },
  });
  const revoke = useMutation({
    mutationFn: (device: PushDevice) =>
      api(`/v1/notifications/devices/${device.id}`, { method: "DELETE" }),
    onMutate: () => reset(),
    onSuccess: (_, device) => {
      setNotice(`« ${device.label} » ne recevra plus de notifications.`);
      refresh();
    },
  });
  const test = useMutation({
    mutationFn: (device: PushDevice) =>
      api<{ queued: boolean }>(`/v1/notifications/devices/${device.id}/test`, { method: "POST" }),
    onMutate: () => reset(),
    onSuccess: (result, device) =>
      setNotice(
        result.queued
          ? `Notification de test envoyée à « ${device.label} » : elle arrive dans la minute.`
          : `Un test est déjà en attente pour « ${device.label} ».`,
      ),
  });
  const preview = useMutation({
    mutationFn: ({ device, value }: { device: PushDevice; value: PushPreview }) =>
      withReauth(
        () =>
          api<PushDevice>(`/v1/notifications/devices/${device.id}`, {
            method: "PATCH",
            body: { preview: value },
          }),
        loginWithPasskey,
      ),
    onMutate: () => reset(),
    onSuccess: refresh,
  });

  const list = devices.data ?? [];
  const current = list.find((d) => d.current) ?? null;
  const activeHere = subscribed && current !== null;
  const error = enable.error ?? disable.error ?? revoke.error ?? test.error ?? preview.error;
  const busy = enable.isPending || disable.isPending;

  let body;
  if (config.isPending) {
    body = <Spinner />;
  } else if (!enabledOnServer) {
    body = (
      <Empty>
        {isApiError(config.error, "push_not_configured")
          ? "Les notifications sur le téléphone ne sont pas activées sur ce serveur."
          : errorMessage(config.error)}
      </Empty>
    );
  } else {
    body = (
      <>
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Recevez rappels et alertes même quand LifeOS est fermé. Par défaut, l&apos;écran
          verrouillé n&apos;affiche qu&apos;un texte générique (« Nouveau rappel »), jamais le
          contenu.
        </p>
        {homeScreen && (
          <Alert tone="info">
            Sur iPhone et iPad, ajoutez d&apos;abord LifeOS à l&apos;écran d&apos;accueil (bouton
            Partager, puis « Sur l&apos;écran d&apos;accueil »), ouvrez-le depuis son icône, puis
            activez les notifications ici (iOS 16.4 ou plus récent).
          </Alert>
        )}
        {supported === false && !homeScreen && (
          <Alert tone="warning">Ce navigateur ne permet pas les notifications push.</Alert>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          {activeHere ? (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => disable.mutate(current?.id ?? null)}
            >
              Désactiver sur cet appareil
            </Button>
          ) : (
            <Button disabled={busy || !supported || homeScreen} onClick={() => enable.mutate()}>
              Activer sur cet appareil
            </Button>
          )}
        </div>

        <h3 className="mt-4 text-sm font-medium">Appareils qui reçoivent les notifications</h3>
        {devices.isPending ? (
          <Spinner />
        ) : list.length === 0 ? (
          <Empty>Aucun appareil pour l&apos;instant.</Empty>
        ) : (
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {list.map((d) => (
              <li key={d.id} className="flex flex-col gap-2 py-2">
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0">
                    <span className="font-medium">{d.label}</span>{" "}
                    {d.current && <Badge tone="teal">Cet appareil</Badge>}
                    <span className="block text-xs text-muted">
                      {d.last_success_at
                        ? `Dernier envoi réussi le ${formatDateTime(d.last_success_at)}`
                        : "Aucun envoi réussi pour l'instant"}
                      {d.failure_count > 0 && ` · ${d.failure_count} échec(s)`}
                    </span>
                  </span>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Envoyer un test à « ${d.label} »`}
                      disabled={test.isPending}
                      onClick={() => test.mutate(d)}
                    >
                      Envoyer un test
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Révoquer « ${d.label} »`}
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(d)}
                    >
                      Révoquer
                    </Button>
                  </div>
                </div>
                <label className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={d.preview === "full"}
                    disabled={preview.isPending}
                    onChange={(e) =>
                      preview.mutate({ device: d, value: e.target.checked ? "full" : "generic" })
                    }
                  />
                  Afficher le titre sur l&apos;écran verrouillé de « {d.label} »
                </label>
              </li>
            ))}
          </ul>
        )}
      </>
    );
  }

  return (
    <Card title="Notifications sur le téléphone" className={className}>
      {body}
      {notice && <Alert tone="success">{notice}</Alert>}
      {error && <Alert tone="error">{errorMessage(error)}</Alert>}
    </Card>
  );
}
