"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card } from "@/components/ui";
import { installMethod, promptInstall, useInstallPrompt, type InstallMethod } from "@/lib/pwa";

/**
 * Réglages → « Installer LifeOS sur le téléphone » (ADR 0022): the installed web app is the
 * mobile client. Chrome and Edge offer their install dialog; Safari on iPhone and iPad and
 * the other browsers get the steps.
 */
export function InstallSection() {
  const state = useInstallPrompt();
  // Read after mounting: the server render knows nothing of the browser.
  const [method, setMethod] = useState<InstallMethod | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setMethod(installMethod(state));
  }, [state]);

  const install = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const outcome = await promptInstall();
      setNotice(
        outcome === "accepted"
          ? "LifeOS s'installe : ouvrez-le ensuite depuis son icône."
          : "Installation annulée. Vous pourrez l'installer plus tard depuis le menu du navigateur.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Installer LifeOS sur le téléphone">
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-stone-600 dark:text-stone-400">
          Installé, LifeOS s&apos;ouvre depuis l&apos;écran d&apos;accueil comme une application : notifications
          (obligatoire sur iPhone et iPad), « Partager → LifeOS » pour envoyer une photo de ticket ou un PDF (Android),
          raccourcis sur l&apos;icône. Rien n&apos;est conservé sur le téléphone : hors connexion ou sans VPN, LifeOS
          affiche seulement une page qui le dit.
        </p>
        {method === "installed" && <Alert tone="success">LifeOS est ouvert en application installée.</Alert>}
        {method === "prompt" && (
          <div>
            <Button disabled={busy} onClick={() => void install()}>
              Installer LifeOS
            </Button>
          </div>
        )}
        {method === "ios" && (
          <ol className="list-inside list-decimal space-y-1">
            <li>Ouvrez LifeOS dans Safari.</li>
            <li>Touchez le bouton Partager, puis « Sur l&apos;écran d&apos;accueil ».</li>
            <li>Ouvrez LifeOS depuis sa nouvelle icône et reconnectez-vous avec votre passkey.</li>
          </ol>
        )}
        {method === "menu" && (
          <p>
            Dans le menu du navigateur, choisissez « Installer l&apos;application » ou « Ajouter à l&apos;écran
            d&apos;accueil ». Sur Android, utilisez Chrome ou Edge.
          </p>
        )}
        {notice && <Alert tone="info">{notice}</Alert>}
      </div>
    </Card>
  );
}
