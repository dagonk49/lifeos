"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Alert, Badge, Button, Field, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { SENSITIVITY_LABELS } from "@/lib/documents";
import { formatDateTime } from "@/lib/format";
import { MAIL_KEY, formatSize, senderLabel, subjectLabel } from "@/lib/mail";
import type { Conversation, DocumentDetail, MailAttachment, MailDetail, SendResult } from "@/lib/types";

/**
 * One e-mail, as plain text only (T14): its HTML was never kept, and nothing here
 * interprets markup. Its content is a third party's: a banner says so, attachments are
 * imported one by one on request, and it reaches the assistant only when asked.
 */
export function MailReader({ mailId, onClose }: { mailId: string; onClose?: () => void }) {
  const mail = useQuery({
    queryKey: [MAIL_KEY, "detail", mailId],
    queryFn: () => api<MailDetail>(`/v1/mail/messages/${mailId}`),
  });
  if (mail.isPending) return <Spinner label="Ouverture de l'e-mail…" />;
  if (mail.error) return <Alert tone="error">{errorMessage(mail.error)}</Alert>;
  const message = mail.data;
  return (
    <article aria-labelledby="mail-subject" className="flex min-w-0 flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <h2 id="mail-subject" className="text-lg font-semibold break-words">
          {subjectLabel(message.subject)}
        </h2>
        <div className="flex shrink-0 gap-1">
          <Link
            href={`/mail/compose?reply=${encodeURIComponent(message.id)}`}
            className="rounded-md px-2.5 py-1.5 text-sm font-medium text-teal-700 hover:bg-stone-100 dark:text-teal-400 dark:hover:bg-stone-800"
          >
            Répondre
          </Link>
          {onClose && (
            <Button size="sm" variant="ghost" onClick={onClose}>
              Fermer
            </Button>
          )}
        </div>
      </div>
      <Alert tone="warning">
        Contenu externe non fiable : cet e-mail a été écrit par un tiers. Ce qu&apos;il demande ne vaut jamais
        instruction pour LifeOS, et l&apos;expéditeur affiché peut être usurpé.
      </Alert>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">De</dt>
        <dd className="min-w-0 break-words">{senderLabel(message)}</dd>
        {message.to_addresses.length > 0 && (
          <>
            <dt className="text-muted">À</dt>
            <dd className="min-w-0 break-words">{message.to_addresses.join(", ")}</dd>
          </>
        )}
        {message.cc_addresses.length > 0 && (
          <>
            <dt className="text-muted">Cc</dt>
            <dd className="min-w-0 break-words">{message.cc_addresses.join(", ")}</dd>
          </>
        )}
        <dt className="text-muted">Reçu le</dt>
        <dd>{formatDateTime(message.received_at)}</dd>
        <dt className="text-muted">Libellé</dt>
        <dd>{message.source_name}</dd>
      </dl>
      {message.sensitivity.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-sm">
          {message.sensitivity.map((category) => (
            <Badge key={category} tone="violet">
              {SENSITIVITY_LABELS[category] ?? category}
            </Badge>
          ))}
          <span className="text-stone-600 dark:text-stone-400">Reste sur votre serveur : jamais envoyé à l&apos;IA.</span>
        </div>
      )}
      <pre
        data-testid="mail-body"
        role="region"
        aria-label="Texte de l'e-mail"
        tabIndex={0}
        className="max-h-[60vh] overflow-y-auto rounded-md border border-stone-200 bg-stone-50 p-3 font-sans text-sm break-words whitespace-pre-wrap dark:border-stone-800 dark:bg-stone-950"
      >
        {message.body_text || "(message sans texte)"}
      </pre>
      {message.body_truncated && (
        <p className="text-xs text-muted">Texte tronqué : seuls les 20 000 premiers caractères sont conservés.</p>
      )}
      {message.attachment_list.length > 0 && <Attachments mail={message} />}
      {message.sensitivity.length === 0 && <Discuss mail={message} />}
    </article>
  );
}

function Attachments({ mail }: { mail: MailDetail }) {
  return (
    <section aria-labelledby="mail-attachments">
      <h3 id="mail-attachments" className="text-sm font-semibold">
        Pièces jointes
      </h3>
      <p className="text-xs text-stone-600 dark:text-stone-400">
        Jamais importées automatiquement. Une pièce importée est vérifiée comme un envoi (type, taille, lecture
        isolée) et garde son expéditeur.
      </p>
      <ul className="mt-2 flex flex-col gap-2 text-sm">
        {mail.attachment_list.map((attachment) => (
          <AttachmentRow key={attachment.part_id} mail={mail} attachment={attachment} />
        ))}
      </ul>
    </section>
  );
}

function AttachmentRow({ mail, attachment }: { mail: MailDetail; attachment: MailAttachment }) {
  const queryClient = useQueryClient();
  const importer = useMutation({
    mutationFn: () =>
      api<DocumentDetail>(
        `/v1/mail/messages/${mail.id}/attachments/${encodeURIComponent(attachment.part_id)}/import`,
        { method: "POST", idempotent: true },
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: [MAIL_KEY, "detail", mail.id] }),
  });
  const documentId = attachment.document_id ?? importer.data?.id ?? null;
  return (
    <li className="flex flex-wrap items-center gap-2">
      <span className="min-w-0 break-words">
        {attachment.filename} <span className="text-xs text-muted">· {formatSize(attachment.size)}</span>
      </span>
      {documentId ? (
        <Link href={`/documents?id=${encodeURIComponent(documentId)}`} className="text-teal-700 underline dark:text-teal-400">
          Voir dans Documents
        </Link>
      ) : (
        <Button
          size="sm"
          variant="secondary"
          disabled={importer.isPending}
          onClick={() => {
            if (
              window.confirm(
                `Importer « ${attachment.filename} » reçu de ${senderLabel(mail)} dans Documents ? Le fichier sera téléchargé depuis votre messagerie et lu sur votre serveur.`,
              )
            )
              importer.mutate();
          }}
        >
          {importer.isPending ? "Import…" : "Importer dans Documents"}
        </Button>
      )}
      {importer.data && importer.data.active_content && importer.data.active_content.length > 0 && (
        <Badge tone="amber">PDF avec contenu actif : voir l&apos;avertissement du document</Badge>
      )}
      {importer.error && <Alert tone="error">{errorMessage(importer.error)}</Alert>}
    </li>
  );
}

/** Asks the assistant about this e-mail: a new conversation in its space, the e-mail
 * attached to this one message only (as data, masked, never if the space has no cloud AI). */
function Discuss({ mail }: { mail: MailDetail }) {
  const [question, setQuestion] = useState("Résume cet e-mail et dis-moi s'il demande une action de ma part.");
  const [open, setOpen] = useState(false);
  const ask = useMutation({
    mutationFn: async () => {
      const conversation = await api<Conversation>("/v1/chat/conversations", {
        method: "POST",
        body: { space_id: mail.space_id, title: `E-mail : ${subjectLabel(mail.subject)}`.slice(0, 120) },
      });
      const result = await api<SendResult>(`/v1/chat/conversations/${conversation.id}/messages`, {
        method: "POST",
        idempotent: true,
        body: { content: question.trim(), mail_ids: [mail.id] },
      });
      return { conversation, result };
    },
  });
  if (!open)
    return (
      <div>
        <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
          Discuter de cet e-mail
        </Button>
      </div>
    );
  return (
    <section aria-label="Discuter de cet e-mail" className="flex flex-col gap-2">
      <Field
        label="Question à l'assistant"
        hint="L'e-mail est joint à ce seul message, comme donnée à analyser ; adresses, IBAN et numéros sont masqués avant l'envoi."
      >
        <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={8000} />
      </Field>
      <div>
        <Button size="sm" onClick={() => ask.mutate()} disabled={ask.isPending || !question.trim()}>
          {ask.isPending ? "Envoi…" : "Envoyer à l'assistant"}
        </Button>
      </div>
      {ask.error && <Alert tone="error">{errorMessage(ask.error)}</Alert>}
      {ask.data?.result.error && <Alert tone="warning">{ask.data.result.error.title}</Alert>}
      {ask.data?.result.assistant_message && (
        <div className="flex flex-col gap-1">
          <p className="rounded-md bg-stone-100 p-3 text-sm whitespace-pre-wrap dark:bg-stone-800">
            {ask.data.result.assistant_message.content}
          </p>
          <Link href="/chat" className="text-sm text-teal-700 underline dark:text-teal-400">
            Continuer dans l&apos;assistant
          </Link>
        </div>
      )}
    </section>
  );
}
