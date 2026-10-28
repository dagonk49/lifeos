"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button } from "@/components/ui";
import { download, errorMessage, withReauth } from "@/lib/api";
import { loginWithPasskey } from "@/lib/passkeys";

export const ARCHIVE_PATH = "/v1/privacy/export.zip";
export const JSON_PATH = "/v1/privacy/export";

/**
 * « Mes données » (Réglages): the archive of all the owner's data, readable by other tools.
 * Every export asks for a recent passkey sign-in (the whole of the owner's data leaves the
 * server): the file is fetched, the passkey asked for if needed, then saved.
 */
export function DataExport() {
  const [originals, setOriginals] = useState(false);
  const exported = useMutation({
    mutationFn: (path: string) =>
      withReauth(
        () =>
          download(
            path,
            path === JSON_PATH ? "lifeos-export.json" : "lifeos-export.zip",
          ),
        loginWithPasskey,
      ),
  });

  return (
    <div className="space-y-3 text-sm">
      <p>
        Une archive ZIP de toutes vos données : le JSON complet, les tâches et
        les opérations en CSV (tableur), les tâches et l&apos;agenda en
        iCalendar (ICS), le journal en Markdown, et un fichier README qui décrit
        chaque format.
      </p>
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={originals}
          onChange={(event) => {
            setOriginals(event.target.checked);
            exported.reset();
          }}
        />
        <span>
          Inclure les originaux déchiffrés de mes documents
          <span className="block text-xs text-stone-500">
            Les fichiers originaux, tels que vous les avez envoyés.
          </span>
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          onClick={() =>
            exported.mutate(
              originals ? `${ARCHIVE_PATH}?originals=true` : ARCHIVE_PATH,
            )
          }
          disabled={exported.isPending}
        >
          {exported.isPending
            ? "Préparation de l'archive…"
            : "Télécharger l'archive (ZIP)"}
        </Button>
        <Button
          variant="secondary"
          onClick={() => exported.mutate(JSON_PATH)}
          disabled={exported.isPending}
        >
          JSON seul
        </Button>
      </div>
      <p className="text-xs text-stone-500">
        Confirmation par passkey si vous ne vous êtes pas connecté récemment ;
        chaque export est inscrit au journal d&apos;audit.
      </p>
      {exported.isError && (
        <Alert tone="error">{errorMessage(exported.error)}</Alert>
      )}
      {exported.isSuccess && (
        <Alert tone="success">Fichier téléchargé : {exported.data}</Alert>
      )}
      <Alert tone="warning">
        Les fichiers téléchargés ne sont pas chiffrés : ils contiennent votre
        journal, vos conversations et le texte de vos documents. Gardez-les sur
        un support chiffré, hors d&apos;un dossier synchronisé dans le cloud.
      </Alert>
      <p className="text-xs text-stone-500">
        Chaque original peut aussi se télécharger seul depuis la page Documents.
        Les jetons des comptes connectés et les clés des passkeys ne sont jamais
        exportés.
      </p>
    </div>
  );
}
