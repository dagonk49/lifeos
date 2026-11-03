"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useId, useRef } from "react";

import { Alert, Badge, Button } from "@/components/ui";
import { errorMessage, upload } from "@/lib/api";
import { MAX_ATTACHMENTS, attachmentStateLabel, type AttachmentState } from "@/lib/attachments";
import type { DocumentDetail, DocumentSummary } from "@/lib/types";

const TONES = {
  reading: "neutral",
  postponed: "amber",
  ready: "teal",
  sensitive: "red",
  local: "red",
  unreadable: "amber",
  unavailable: "amber",
} as const;

/**
 * Attachments of the message being written: each file is uploaded as a document (encrypted,
 * read locally), and only its extracted text goes with the message, never a sensitive one.
 * The parent polls the attached documents and passes them, and their states, in the order
 * of `ids`; it also learns when an upload is in flight, to hold the message until then.
 */
export function ChatAttachments({
  spaceId,
  conversationId,
  ids,
  documents,
  states,
  onAdd,
  onRemove,
  onUploadingChange,
}: {
  spaceId: string | null;
  conversationId: string | null;
  ids: string[];
  documents: (DocumentSummary | undefined)[];
  states: AttachmentState[];
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
  onUploadingChange?: (uploading: boolean) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const add = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("space_id", spaceId ?? "");
      form.append("purpose", "attachment");
      if (conversationId) {
        form.append("source_type", "conversation");
        form.append("source_id", conversationId);
      }
      form.append("file", file, file.name);
      return upload<DocumentDetail>("/v1/documents", form, { idempotent: true });
    },
  });
  // The page holds the message while a file is on its way (it would be left out).
  const uploading = add.isPending;
  useEffect(() => {
    onUploadingChange?.(uploading);
  }, [uploading, onUploadingChange]);
  useEffect(() => () => onUploadingChange?.(false), [onUploadingChange]);

  return (
    <div className="flex flex-col gap-2">
      {ids.length > 0 && (
        <ul aria-label="Pièces jointes" className="flex flex-col gap-1">
          {ids.map((id, index) => {
            const document = documents[index];
            const state = states[index] ?? "reading";
            return (
              <li
                key={id}
                className="flex flex-wrap items-center gap-2 rounded border border-stone-200 px-2 py-1 text-sm dark:border-stone-800"
              >
                <span className="font-medium">{document?.title ?? "Document"}</span>
                <Badge tone={TONES[state]}>{attachmentStateLabel(state, document?.sensitivity)}</Badge>
                {/* A new tab: the message being written stays as it is. */}
                <Link
                  className="text-xs underline"
                  href={`/documents?id=${encodeURIComponent(id)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Vérifier le texte<span className="sr-only"> (nouvel onglet)</span>
                </Link>
                <button type="button" className="text-xs underline" onClick={() => onRemove(id)}>
                  Retirer
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex items-center gap-2">
        <input
          ref={input}
          id={inputId}
          type="file"
          className="sr-only"
          // Opened by the « Joindre un document » button: kept out of the tab order, but named.
          tabIndex={-1}
          aria-label="Document à joindre"
          accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,.heic,text/plain"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            // Per call: not run if the composer is gone by the end of the upload.
            if (file) add.mutate(file, { onSuccess: (document) => onAdd(document.id) });
          }}
        />
        <Button
          size="sm"
          variant="secondary"
          onClick={() => input.current?.click()}
          disabled={add.isPending || ids.length >= MAX_ATTACHMENTS || !spaceId}
        >
          {add.isPending ? "Envoi du document…" : "Joindre un document"}
        </Button>
        <span className="text-xs text-muted">
          Lu sur votre serveur ; seul le texte non sensible accompagne le message.
        </span>
      </div>
      {add.error && <Alert tone="error">{errorMessage(add.error)}</Alert>}
    </div>
  );
}
