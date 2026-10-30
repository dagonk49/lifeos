"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { DocumentStatusBadge } from "@/components/DocumentList";
import { Alert, Badge, Button, Field, Input, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage, isApiError, withReauth } from "@/lib/api";
import {
  canDownloadOriginal,
  canEditText,
  detailPollInterval,
  documentKeys,
  documentPath,
  financesReceiptHref,
  formatConfidence,
  formatFileSize,
  formatMoney,
  isInProgress,
  isLowConfidence,
  KIND_LABELS,
  originalLabel,
  parseTitle,
  PURPOSE_LABELS,
  readReceipt,
  retentionHint,
  SENSITIVITY_LABELS,
  sensitivityDetails,
  sourceLink,
  statusInfo,
  textSourceLabel,
  TITLE_MAX,
  validateText,
} from "@/lib/documents";
import { formatDate, formatDateTime } from "@/lib/format";
import { activeContentList } from "@/lib/mail";
import { loginWithPasskey } from "@/lib/passkeys";
import { useSpace } from "@/lib/space";
import type { DocumentDetail } from "@/lib/types";

const PANEL =
  "rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900";
const LINK_BUTTON = "inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-medium transition-colors";
const LINK_PRIMARY = `${LINK_BUTTON} bg-teal-700 px-3.5 py-2 text-white hover:bg-teal-800`;
const LINK_SECONDARY = `${LINK_BUTTON} border border-stone-300 bg-white px-2.5 py-1 text-stone-800 hover:bg-stone-100 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:hover:bg-stone-800`;

function Section({ title, children }: { title?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section
      aria-labelledby={title ? id : undefined}
      className="mt-4 border-t border-stone-100 pt-4 dark:border-stone-800"
    >
      {title && (
        <h3 id={id} className="mb-2 text-sm font-semibold">
          {title}
        </h3>
      )}
      {children}
    </section>
  );
}

interface PatchBody {
  title?: string;
  extracted_text?: string;
  keep_original?: boolean;
}

/** PATCH with the version read (If-Match); a conflict reloads the document. */
function usePatchDocument(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, version }: { body: PatchBody; version: number }) =>
      api<DocumentDetail>(documentPath(documentId), {
        method: "PATCH",
        body,
        headers: { "If-Match": String(version) },
      }),
    onSuccess: (document) => queryClient.setQueryData(documentKeys.detail(documentId), document),
    onError: (error) => {
      if (
        isApiError(error, "version_conflict") ||
        isApiError(error, "document_processing") ||
        isApiError(error, "original_purged")
      ) {
        void queryClient.invalidateQueries({ queryKey: documentKeys.detail(documentId) });
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists });
      void queryClient.invalidateQueries({ queryKey: documentKeys.receiptProposal(documentId) });
      void queryClient.invalidateQueries({ queryKey: ["search"] });
    },
  });
}

function TitleEditor({ document, onDone }: { document: DocumentDetail; onDone: () => void }) {
  const patch = usePatchDocument(document.id);
  const [value, setValue] = useState(document.title);
  const [invalid, setInvalid] = useState<string | null>(null);
  return (
    <form
      className="mt-3 flex flex-col gap-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const parsed = parseTitle(value, document.title);
        if (!parsed.ok) {
          setInvalid(parsed.error);
          return;
        }
        if (parsed.value === null) {
          onDone();
          return;
        }
        patch.mutate({ body: { title: parsed.value }, version: document.version }, { onSuccess: onDone });
      }}
    >
      <Field label="Titre du document">
        <Input
          value={value}
          maxLength={TITLE_MAX}
          autoFocus
          onChange={(e) => {
            setInvalid(null);
            setValue(e.target.value);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={patch.isPending}>
          {patch.isPending ? "Enregistrement…" : "Enregistrer le titre"}
        </Button>
        <Button variant="secondary" size="sm" onClick={onDone}>
          Annuler
        </Button>
      </div>
      {(invalid || patch.error) && <Alert tone="error">{invalid ?? errorMessage(patch.error)}</Alert>}
    </form>
  );
}

function SensitivityBlock({ document }: { document: DocumentDetail }) {
  const titleId = useId();
  const details = sensitivityDetails(document);
  if (details.length === 0) return null;
  return (
    <section
      aria-labelledby={titleId}
      className="mt-4 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-sm text-violet-950 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-100"
    >
      <h3 id={titleId} className="font-semibold">
        Document sensible
      </h3>
      <ul className="mt-1 list-disc pl-5">
        {details.map((detail) => (
          <li key={detail.category}>
            <span className="font-medium">{detail.label}</span>
            {detail.reasons.length > 0 && ` : ${detail.reasons.join(", ")}`}
          </li>
        ))}
      </ul>
      <p className="mt-1">Ce document est sensible : son contenu n&apos;est jamais envoyé à l&apos;assistant IA.</p>
    </section>
  );
}

function ReceiptBlock({ document }: { document: DocumentDetail }) {
  const reading = readReceipt(document.analysis);
  if (!reading) {
    return document.status === "ready" ? (
      <Section title="Lecture du ticket">
        <p className="text-sm text-muted">Aucune information n&apos;a pu être lue sur ce ticket.</p>
      </Section>
    ) : null;
  }
  const rows: [string, string | null, string | null][] = [
    ["Commerçant", reading.merchant, reading.lines.merchant],
    ["Date", reading.txDate ? formatDate(reading.txDate) : null, reading.lines.txDate],
    ["Total", reading.total ? formatMoney(reading.total, reading.currency) : null, reading.lines.total],
  ];
  return (
    <Section title="Lecture du ticket">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
        {rows.map(([label, value, line]) => (
          <div key={label} className="contents">
            <dt className="text-muted">{label}</dt>
            <dd className="min-w-0">
              {value ? <span className="font-medium">{value}</span> : <span className="text-muted">Non trouvé</span>}
              {line && (
                <span className="block text-xs break-words text-muted">
                  Ligne lue : « <span className="font-mono">{line}</span> »
                </span>
              )}
            </dd>
          </div>
        ))}
        {reading.cardPayment && (
          <div className="contents">
            <dt className="text-muted">Paiement</dt>
            <dd>Par carte</dd>
          </div>
        )}
        {reading.confidence !== null && (
          <div className="contents">
            <dt className="text-muted">Confiance</dt>
            <dd>{formatConfidence(reading.confidence)}</dd>
          </div>
        )}
      </dl>
      {reading.warnings.length > 0 && (
        <div className="mt-2">
          <Alert tone="warning">
            <ul className="list-disc pl-4">
              {reading.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Alert>
        </div>
      )}
      {document.status === "ready" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Link href={financesReceiptHref(document.id)} className={LINK_PRIMARY}>
            Ajouter aux finances
          </Link>
          <span className="text-xs text-muted">Vous vérifiez et corrigez les valeurs avant tout enregistrement.</span>
        </div>
      )}
    </Section>
  );
}

function OriginalControl({ document }: { document: DocumentDetail }) {
  const patch = usePatchDocument(document.id);
  const inputId = useId();
  const hintId = useId();
  if (!document.original_available) {
    return <p className="text-sm text-stone-600 dark:text-stone-400">{retentionHint(document)}</p>;
  }
  // Optimistic while the change is being saved.
  const checked = patch.isPending ? Boolean(patch.variables?.body.keep_original) : document.keep_original;
  return (
    <div className="flex flex-col gap-2">
      {/* The hint stays outside the <label>: the checkbox's accessible name is the label alone. */}
      <div className="flex items-start gap-2 text-sm">
        <input
          id={inputId}
          type="checkbox"
          className="mt-0.5 size-4 shrink-0 accent-teal-700"
          checked={checked}
          disabled={patch.isPending}
          aria-describedby={hintId}
          onChange={(e) => patch.mutate({ body: { keep_original: e.target.checked }, version: document.version })}
        />
        <div>
          <label htmlFor={inputId} className="font-medium">
            Conserver l&apos;original sans limite
          </label>
          <p id={hintId} className="mt-0.5 text-xs text-muted">
            {retentionHint(document)}
          </p>
        </div>
      </div>
      {patch.error && <Alert tone="error">{errorMessage(patch.error)}</Alert>}
    </div>
  );
}

/**
 * « Toujours garder en local »: the owner's own refusal, on top of the detection. Adding it
 * is immediate; lifting it lets the document go to the assistant again, so the server asks
 * for a passkey (reinforced operation). A new reading or a correction never changes it.
 */
function LocalOnlyControl({ document }: { document: DocumentDetail }) {
  const queryClient = useQueryClient();
  const inputId = useId();
  const hintId = useId();
  // The box follows the click at once (a controlled input would otherwise flick back until
  // the mutation reports itself pending); the server's answer then replaces this value.
  const [requested, setRequested] = useState<boolean | null>(null);
  const change = useMutation({
    mutationFn: (localOnly: boolean) =>
      withReauth(
        () =>
          api<DocumentDetail>(documentPath(document.id), {
            method: "PATCH",
            body: { local_only: localOnly },
            headers: { "If-Match": String(document.version) },
          }),
        loginWithPasskey,
      ),
    onSuccess: (updated) => queryClient.setQueryData(documentKeys.detail(document.id), updated),
    onError: (error) => {
      if (isApiError(error, "version_conflict")) {
        void queryClient.invalidateQueries({ queryKey: documentKeys.detail(document.id) });
      }
    },
    onSettled: () => {
      setRequested(null);
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists });
    },
  });
  // Optimistic while the change is being saved; back to the server's value on failure.
  const checked = requested ?? document.local_only;
  const sensitive = document.sensitivity.length > 0;
  const hint = checked
    ? "Son contenu n'est jamais envoyé à l'assistant IA, même après une correction du texte ou une nouvelle lecture. Le décocher demande une confirmation par passkey."
    : sensitive
      ? "Déjà détecté comme sensible : il reste sur votre serveur. Cochez pour qu'il y reste même si une correction du texte lève la détection."
      : "Sinon, son texte peut accompagner un message à l'assistant IA quand vous le joignez (identifiants masqués).";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-2 text-sm">
        <input
          id={inputId}
          type="checkbox"
          className="mt-0.5 size-4 shrink-0 accent-teal-700"
          checked={checked}
          disabled={change.isPending}
          aria-describedby={hintId}
          onChange={(e) => {
            setRequested(e.target.checked);
            change.mutate(e.target.checked);
          }}
        />
        <div>
          <label htmlFor={inputId} className="font-medium">
            Toujours garder en local
          </label>
          <p id={hintId} className="mt-0.5 text-xs text-muted">
            {hint}
          </p>
        </div>
      </div>
      {change.error && <Alert tone="error">{errorMessage(change.error)}</Alert>}
    </div>
  );
}

function TextEditor({ document }: { document: DocumentDetail }) {
  const patch = usePatchDocument(document.id);
  // null = not edited: the field shows the server's text (which polling may update).
  const [draft, setDraft] = useState<string | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const editable = canEditText(document.status);
  const serverText = document.extracted_text ?? "";
  const value = editable ? (draft ?? serverText) : serverText;
  const changed = editable && draft !== null && draft !== serverText;
  const source = textSourceLabel(document.text_source, document.text_confidence);

  return (
    <form
      className="flex flex-col gap-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (draft === null) return;
        const error = validateText(draft);
        if (error) {
          setInvalid(error);
          return;
        }
        patch.mutate(
          { body: { extracted_text: draft }, version: document.version },
          {
            onSuccess: () => {
              setDraft(null);
              setSaved(true);
            },
          },
        );
      }}
    >
      {source && <p className="text-sm text-stone-600 dark:text-stone-400">{source}</p>}
      {isLowConfidence(document.text_confidence) && (
        <Alert tone="warning">Vérifiez le texte, la lecture est incertaine.</Alert>
      )}
      <Field
        label="Texte du document"
        hint={
          editable
            ? "Corrigez les erreurs de lecture : votre correction remplace le texte lu, et la détection des données sensibles (comme la lecture d'un ticket) est refaite."
            : "Le texte sera modifiable une fois la lecture terminée."
        }
      >
        <Textarea
          rows={12}
          className="font-mono text-xs"
          value={value}
          disabled={!editable}
          placeholder={editable ? "Aucun texte lu. Vous pouvez saisir ici le texte du document." : undefined}
          onChange={(e) => {
            setInvalid(null);
            setSaved(false);
            patch.reset();
            setDraft(e.target.value);
          }}
        />
      </Field>
      {document.text_corrected_at && (
        <p className="text-xs text-muted">Corrigé le {formatDateTime(document.text_corrected_at)}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={!changed || patch.isPending}>
          {patch.isPending ? "Enregistrement…" : "Enregistrer la correction"}
        </Button>
        {changed && (
          <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>
            Annuler les modifications
          </Button>
        )}
      </div>
      {(invalid || patch.error) && <Alert tone="error">{invalid ?? errorMessage(patch.error)}</Alert>}
      {saved && !changed && <Alert tone="success">Correction enregistrée.</Alert>}
    </form>
  );
}

/** The confirmation shown before downloading a file received from a third party. */
export function downloadWarning(document: { received_from?: string | null; active_content?: string[] }): string {
  const active = activeContentList(document.active_content);
  return (
    `Ce fichier a été reçu par e-mail de ${document.received_from ?? "un tiers"}. Ne l'ouvrez que si vous faites confiance à l'expéditeur.` +
    (active ? ` Il contient du contenu actif (${active}).` : "") +
    " Télécharger quand même ?"
  );
}

/** Detail of one document: reading status, sensitivity, receipt, text correction, original. */
export function DocumentDetailPanel({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { spaceName } = useSpace();
  const [renaming, setRenaming] = useState(false);

  const detail = useQuery({
    queryKey: documentKeys.detail(documentId),
    queryFn: () => api<DocumentDetail>(documentPath(documentId)),
    // The server reads the document in the background: poll until it is done.
    refetchInterval: (query) => detailPollInterval(query.state.data?.status),
  });

  // The reading finished (or restarted): the lists show the status too.
  const status = detail.data?.status;
  const previousStatus = useRef(status);
  useEffect(() => {
    if (previousStatus.current !== undefined && status !== undefined && previousStatus.current !== status) {
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists });
    }
    previousStatus.current = status;
  }, [status, queryClient]);

  const reprocess = useMutation({
    mutationFn: () => api<DocumentDetail>(documentPath(documentId, "/reprocess"), { method: "POST" }),
    onSuccess: (document) => queryClient.setQueryData(documentKeys.detail(documentId), document),
    onError: (error) => {
      if (isApiError(error, "document_processing") || isApiError(error, "original_purged")) {
        void queryClient.invalidateQueries({ queryKey: documentKeys.detail(documentId) });
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists });
      void queryClient.invalidateQueries({ queryKey: documentKeys.receiptProposal(documentId) });
    },
  });

  const remove = useMutation({
    mutationFn: () => api(documentPath(documentId), { method: "DELETE" }),
    onSuccess: () => {
      // Lists only: refetching the deleted document itself would fail.
      void queryClient.invalidateQueries({ queryKey: documentKeys.lists });
      void queryClient.invalidateQueries({ queryKey: ["search"] });
      onClose();
    },
  });

  const closeButton = (
    <Button variant="ghost" size="sm" onClick={onClose} aria-label="Fermer le détail du document">
      Fermer
    </Button>
  );

  if (detail.isPending) {
    return (
      <div className={PANEL}>
        <Spinner />
      </div>
    );
  }
  const loadError =
    detail.error &&
    (isApiError(detail.error, "document_not_found") || isApiError(detail.error, "validation_error")
      ? "Ce document est introuvable ou a été supprimé."
      : errorMessage(detail.error));
  // A deleted document, or a first load that failed. A failed refresh (polling) keeps the
  // last known state and shows the error above it.
  if (!detail.data || isApiError(detail.error, "document_not_found")) {
    return (
      <div className={`${PANEL} flex flex-col items-start gap-2`}>
        <Alert tone="error">{loadError || "Erreur inattendue."}</Alert>
        {closeButton}
      </div>
    );
  }

  const document = detail.data;
  const info = statusInfo(document);
  const inProgress = isInProgress(document.status);
  const source = sourceLink(document.source_type, document.source_id);
  const facts: [string, ReactNode][] = [
    ["Type", PURPOSE_LABELS[document.purpose] ?? document.purpose],
    [
      "Format",
      `${KIND_LABELS[document.kind] ?? document.kind}${document.pages ? ` · ${document.pages} page${document.pages > 1 ? "s" : ""}` : ""}`,
    ],
    ["Taille", formatFileSize(document.size_bytes)],
    ["Espace", spaceName(document.space_id)],
    ["Ajouté le", formatDateTime(document.created_at)],
  ];
  if (document.processed_at) facts.push(["Lu le", formatDateTime(document.processed_at)]);
  facts.push(["Original", originalLabel(document)]);
  if (document.received_from) facts.push(["Pièce jointe reçue de", document.received_from]);
  if (source) {
    facts.push([
      "Lié à",
      <Link key="source" href={source.href} className="text-teal-700 underline dark:text-teal-400">
        {source.label}
      </Link>,
    ]);
  }

  const confirmReprocess = () =>
    window.confirm(
      `Relire « ${document.title} » ? Le texte extrait sera remplacé par une nouvelle lecture${
        document.text_corrected_at ? " : votre correction sera perdue" : ""
      }.`,
    );

  return (
    <section aria-labelledby="document-detail-title" className={PANEL}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="document-detail-title" className="text-lg font-semibold break-words">
            {document.title}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <DocumentStatusBadge status={document.status} />
            {document.sensitivity.map((category) => (
              <Badge key={category} tone="violet">
                {SENSITIVITY_LABELS[category] ?? category}
              </Badge>
            ))}
            {document.local_only && <Badge tone="violet">Gardé en local</Badge>}
          </div>
        </div>
        {closeButton}
      </div>

      {loadError && (
        <div className="mt-3">
          <Alert tone="error">{loadError}</Alert>
        </div>
      )}

      {renaming && <TitleEditor document={document} onDone={() => setRenaming(false)} />}

      {document.received_from && (
        <div className="mt-3">
          <Alert tone="warning">
            Fichier reçu d&apos;un tiers par e-mail : son texte reste une donnée, jamais une instruction. N&apos;ouvrez
            l&apos;original que si vous faites confiance à l&apos;expéditeur.
            {activeContentList(document.active_content) &&
              ` Ce PDF contient du contenu actif (${activeContentList(document.active_content)}) : LifeOS ne l'exécute pas, mais un lecteur PDF sur votre poste pourrait le faire.`}
          </Alert>
        </div>
      )}

      {info && (
        <div className="mt-3">
          <Alert tone={info.tone}>
            {info.text}
            {inProgress && " Cette page se met à jour automatiquement."}
            {document.status === "failed" &&
              ` Vous pouvez saisir le texte du document ci-dessous${document.original_available ? " ou le relire" : ""}.`}
          </Alert>
        </div>
      )}

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        {facts.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted">{label}</dt>
            <dd className="min-w-0">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Actions sur le document">
        {!renaming && (
          <Button size="sm" variant="secondary" onClick={() => setRenaming(true)}>
            Renommer
          </Button>
        )}
        {canDownloadOriginal(document) && (
          <a
            href={documentPath(document.id, "/original")}
            download
            className={LINK_SECONDARY}
            onClick={(event) => {
              // A third party's file: a warning before the original reaches the owner's device.
              if (document.received_from && !window.confirm(downloadWarning(document))) event.preventDefault();
            }}
          >
            Télécharger l&apos;original
          </a>
        )}
        {document.original_available && document.kind !== "audio" && (
          <Button
            size="sm"
            variant="secondary"
            disabled={inProgress || reprocess.isPending}
            onClick={() => {
              if (confirmReprocess()) reprocess.mutate();
            }}
          >
            {reprocess.isPending ? "Relance…" : "Relire le document"}
          </Button>
        )}
        <Button
          size="sm"
          variant="danger"
          disabled={remove.isPending}
          onClick={() => {
            if (
              window.confirm(
                `Supprimer définitivement « ${document.title} » ? Le fichier original chiffré et le texte extrait seront effacés.`,
              )
            ) {
              remove.mutate();
            }
          }}
        >
          Supprimer
        </Button>
      </div>
      {(reprocess.error || remove.error) && (
        <div className="mt-2">
          <Alert tone="error">{errorMessage(reprocess.error ?? remove.error)}</Alert>
        </div>
      )}

      <SensitivityBlock document={document} />
      <Section title="Assistant IA">
        <LocalOnlyControl document={document} />
      </Section>
      {document.purpose === "receipt" && <ReceiptBlock document={document} />}

      <Section title="Texte extrait">
        {/* A new reading (processed_at changes) discards an unsaved draft. */}
        <TextEditor key={document.processed_at ?? "unread"} document={document} />
      </Section>

      <Section title="Fichier original">
        <OriginalControl document={document} />
      </Section>
    </section>
  );
}
