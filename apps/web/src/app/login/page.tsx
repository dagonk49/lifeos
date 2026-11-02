"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Alert, Button, Card, documentTitle } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { loginWithPasskey } from "@/lib/passkeys";
import { safeNext, setSigningOut } from "@/lib/session";

function LoginForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  // Sent here by a lost session (lib/session.ts): back to that page afterwards, if it is one
  // of this site's.
  const next = useSearchParams().get("next");
  const target = safeNext(next);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Nothing of the previous session is kept in memory, and a later sign-out is new.
    queryClient.clear();
    setSigningOut(false);
  }, [queryClient]);

  async function login() {
    setBusy(true);
    setError(null);
    try {
      await loginWithPasskey();
      router.replace(target);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {target !== "/" && (
        <Alert tone="info">
          Session expirée : reconnectez-vous pour reprendre là où vous en étiez.
        </Alert>
      )}
      <p className="text-sm text-stone-600 dark:text-stone-400">
        Connectez-vous avec la passkey enregistrée sur votre appareil.
      </p>
      <Button onClick={login} disabled={busy}>
        {busy ? "Vérification…" : "Se connecter avec une passkey"}
      </Button>
      {error && <Alert tone="error">{error}</Alert>}
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-4">
      <title>{documentTitle("Connexion")}</title>
      <h1 className="text-center text-2xl font-semibold">LifeOS</h1>
      <Card>
        <Suspense>
          <LoginForm />
        </Suspense>
      </Card>
      <p className="text-center text-xs text-muted">
        Nouvel appareil ou accès perdu : demandez un lien d&apos;enrôlement sur le serveur
        (<code>lifeos-admin enrollment-token</code>).
      </p>
    </main>
  );
}
