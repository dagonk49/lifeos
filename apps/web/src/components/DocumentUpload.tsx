"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Alert, Button, Card, Field, Input, Select } from "@/components/ui";
import { upload } from "@/lib/api";
import {
  ACCEPTED_FILES,
  buildUploadForm,
  DEFAULT_RETENTION_DAYS,
  documentKeys,
  formatFileSize,
  retentionHint,
  TITLE_MAX,
  UPLOAD_PURPOSES,
  uploadErrorMessage,
  type UploadPurpose,
} from "@/lib/documents";
import { useSpace } from "@/lib/space";
import type { DocumentDetail } from "@/lib/types";

/** Upload card: the file is encrypted on the server, then read locally in the background. */
export function DocumentUpload({ onUploaded }: { onUploaded: (document: DocumentDetail) => void }) {
  const queryClient = useQueryClient();
  const { spaces, selected, writeSpaceId, spaceName } = useSpace();
  const [file, setFile] = useState<File | null>(null);
  const [purpose, setPurpose] = useState<UploadPurpose>("document");
  const [title, setTitle] = useState("");
  const [keepOriginal, setKeepOriginal] = useState(false);
  // "" = follow the space selected in the header (or the personal space).
  const [spaceId, setSpaceId] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [done, setDone] = useState<DocumentDetail | null>(null);
  // Remounts the file input to clear it after an upload.
  const [inputKey, setInputKey] = useState(0);
  const keepId = useId();
  const keepHintId = useId();
  const fileInfoId = useId();
  const targetSpace = selected ?? (spaceId || writeSpaceId);

  const send = useMutation({
    mutationFn: (form: FormData) => upload<DocumentDetail>("/v1/documents", form, { idempotent: true }),
    onSuccess: (document) => {
      queryClient.setQueryData(documentKeys.detail(document.id), document);
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists });
      setFile(null);
      setTitle("");
      setKeepOriginal(false);
      setInputKey((k) => k + 1);
      setDone(document);
      onUploaded(document);
    },
  });

  const change = <T,>(setter: (value: T) => void) => (value: T) => {
    setInvalid(null);
    setDone(null);
    send.reset();
    setter(value);
  };

  return (
    <Card title="Envoyer un document">
      <form
        className="flex flex-col gap-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const result = buildUploadForm({ file, spaceId: targetSpace, purpose, title, keepOriginal });
          if (!result.ok) {
            setInvalid(result.error);
            return;
          }
          send.mutate(result.value);
        }}
      >
        <Field label="Fichier" hint="PDF, image (JPEG, PNG, WebP, HEIC) ou fichier texte.">
          <input
            key={inputKey}
            type="file"
            accept={ACCEPTED_FILES}
            aria-describedby={file ? fileInfoId : undefined}
            className="text-sm file:mr-3 file:rounded-md file:border file:border-stone-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-stone-800 hover:file:bg-stone-100 dark:file:border-stone-700 dark:file:bg-stone-900 dark:file:text-stone-100"
            onChange={(e) => change(setFile)(e.target.files?.[0] ?? null)}
          />
        </Field>
        {file && (
          <p id={fileInfoId} className="text-sm break-all text-stone-600 dark:text-stone-400">
            Fichier choisi : <span className="font-medium text-stone-800 dark:text-stone-200">{file.name}</span> (
            {formatFileSize(file.size)})
          </p>
        )}

        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1 font-medium text-stone-700 dark:text-stone-300">Type</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {UPLOAD_PURPOSES.map((option) => (
              <label key={option.value} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="document-purpose"
                  className="accent-teal-700"
                  checked={purpose === option.value}
                  onChange={() => change(setPurpose)(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
          {purpose === "receipt" && (
            <span className="text-xs text-stone-500">
              Le montant, la date et le commerçant sont lus localement ; vous les vérifiez avant tout ajout aux finances.
            </span>
          )}
        </fieldset>

        <Field label="Titre (facultatif)" hint="Par défaut : le nom du fichier.">
          <Input value={title} maxLength={TITLE_MAX} onChange={(e) => change(setTitle)(e.target.value)} />
        </Field>

        {selected === null && spaces.length > 1 ? (
          <Field label="Espace">
            <Select value={targetSpace} onChange={(e) => change(setSpaceId)(e.target.value)}>
              {spaces.map((space) => (
                <option key={space.id} value={space.id}>
                  {space.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : (
          spaceName(targetSpace) && (
            <p className="text-sm text-stone-600 dark:text-stone-400">
              Espace : <span className="font-medium text-stone-800 dark:text-stone-200">{spaceName(targetSpace)}</span>
            </p>
          )
        )}

        {/* The hint stays outside the <label>: the checkbox's accessible name is the label alone. */}
        <div className="flex items-start gap-2 text-sm">
          <input
            id={keepId}
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 accent-teal-700"
            checked={keepOriginal}
            onChange={(e) => change(setKeepOriginal)(e.target.checked)}
            aria-describedby={keepHintId}
          />
          <div>
            <label htmlFor={keepId} className="font-medium">
              Conserver l&apos;original sans limite
            </label>
            <p id={keepHintId} className="mt-0.5 text-xs text-stone-500">
              Par défaut, l&apos;original chiffré est supprimé à la fin de la durée de conservation (
              {DEFAULT_RETENTION_DAYS} jours, sauf autre réglage du serveur) ; le texte extrait reste disponible. La date
              exacte s&apos;affiche après l&apos;envoi.
            </p>
          </div>
        </div>

        <div>
          <Button type="submit" disabled={send.isPending || !file}>
            {send.isPending ? "Envoi…" : "Envoyer"}
          </Button>
        </div>

        {(invalid || send.error) && <Alert tone="error">{invalid ?? uploadErrorMessage(send.error)}</Alert>}
        {done && (
          <Alert tone="success">
            « {done.title} » est envoyé : lecture en cours. {retentionHint(done)}
          </Alert>
        )}
      </form>
    </Card>
  );
}
