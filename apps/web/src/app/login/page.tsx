"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, Button, Card } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { loginWithPasskey } from "@/lib/passkeys";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function login() {
    setBusy(true);
    setError(null);
    try {
      await loginWithPasskey();
      router.replace("/");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-4">
      <h1 className="text-center text-2xl font-semibold">LifeOS</h1>
      <Card>
        <div className="flex flex-col gap-3">
          <p className="text-sm text-stone-600 dark:text-stone-400">
            Connectez-vous avec la passkey enregistrée sur votre appareil.
          </p>
          <Button onClick={login} disabled={busy}>
            {busy ? "Vérification…" : "Se connecter avec une passkey"}
          </Button>
          {error && <Alert tone="error">{error}</Alert>}
        </div>
      </Card>
      <p className="text-center text-xs text-stone-500">
        Nouvel appareil ou accès perdu : demandez un lien d&apos;enrôlement sur le serveur
        (<code>lifeos-admin enrollment-token</code>).
      </p>
    </main>
  );
}
