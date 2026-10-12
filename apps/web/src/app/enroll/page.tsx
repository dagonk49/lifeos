"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { registerPasskey } from "@/lib/passkeys";

function guessDeviceName(): string {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return "Téléphone Android";
  if (/iPhone|iPad/i.test(ua)) return "iPhone";
  if (/Mac/i.test(ua)) return "Mac";
  if (/Windows/i.test(ua)) return "PC Windows";
  return "Mon appareil";
}

export default function EnrollPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // The token travels in the URL fragment: never sent to the server or logged.
    const params = new URLSearchParams(window.location.hash.slice(1));
    setToken(params.get("token"));
    setLabel(guessDeviceName());
    window.history.replaceState(null, "", "/enroll");
  }, []);

  async function enroll() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await registerPasskey(label.trim() || "Passkey", token);
      router.replace("/");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-4">
      <h1 className="text-center text-2xl font-semibold">Enregistrer cet appareil</h1>
      <Card>
        {token === undefined ? null : token === null ? (
          <Alert tone="warning">
            Lien incomplet. Ouvrez le lien d&apos;enrôlement fourni par <code>lifeos-admin</code>.
          </Alert>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void enroll();
            }}
          >
            <p className="text-sm text-stone-600 dark:text-stone-400">
              Une passkey va être créée sur cet appareil. Elle remplace les mots de passe et
              exige votre empreinte, votre visage ou le code de l&apos;appareil.
            </p>
            <Field label="Nom de l'appareil">
              <Input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} />
            </Field>
            <Button type="submit" disabled={busy}>
              {busy ? "Création…" : "Créer ma passkey"}
            </Button>
            {error && <Alert tone="error">{error}</Alert>}
          </form>
        )}
      </Card>
    </main>
  );
}
