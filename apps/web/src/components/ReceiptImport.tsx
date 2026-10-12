"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { TransactionForm } from "@/components/TransactionForm";
import { Alert, Badge, Button, Card, Field, Select, Spinner } from "@/components/ui";
import { api, isApiError, upload } from "@/lib/api";
import {
  confidenceLabel,
  confidenceTone,
  emptyTransactionForm,
  financeErrorMessage,
  financeKeys,
  formatMoney,
  isReading,
  LOW_CONFIDENCE,
  POLL_MS,
  proposalPath,
  proposalToForm,
  readingDelay,
  readingPollInterval,
  receiptFailure,
  signedAmount,
} from "@/lib/finance";
import { formatDate } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { DocumentDetail, ReceiptProposal, Transaction } from "@/lib/types";

interface Callbacks {
  today: string;
  onRecorded: (transaction: Transaction) => void;
  onShowTransaction?: (transaction: Transaction) => void;
  onClose: () => void;
}

function notFound(error: unknown): boolean {
  return isApiError(error, "document_not_found") || isApiError(error, "validation_error");
}

/** Entry B: a photo (or PDF) of the receipt, uploaded as a "receipt" document. */
function UploadStep({ defaultSpaceId, onUploaded }: { defaultSpaceId?: string | null; onUploaded: (id: string) => void }) {
  const queryClient = useQueryClient();
  const { spaces, selected } = useSpace();
  const [space, setSpace] = useState(
    () => selected ?? defaultSpaceId ?? (spaces.length === 1 ? (spaces[0]?.id ?? "") : ""),
  );
  const [file, setFile] = useState<File | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  const send = useMutation({
    mutationFn: ({ file: picked, spaceId }: { file: File; spaceId: string }) => {
      const form = new FormData();
      form.append("space_id", spaceId);
      form.append("purpose", "receipt");
      form.append("file", picked);
      return upload<DocumentDetail>("/v1/documents", form, { idempotent: true });
    },
    onSuccess: (document) => {
      queryClient.setQueryData(financeKeys.document(document.id), document);
      void queryClient.invalidateQueries({ queryKey: ["documents", "list"] });
    },
  });

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const spaceId = selected ?? space;
        if (!spaceId) return setInvalid("Choisissez l'espace du ticket.");
        if (!file) return setInvalid("Choisissez une photo ou un PDF du ticket.");
        // Per call: no navigation if the import was closed during the upload.
        send.mutate({ file, spaceId }, { onSuccess: (document) => onUploaded(document.id) });
      }}
    >
      {!selected && (
        <Field label="Espace">
          <Select
            value={space}
            onChange={(e) => {
              setInvalid(null);
              setSpace(e.target.value);
            }}
          >
            <option value="">Choisissez un espace</option>
            {spaces.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <Field label="Photo ou PDF du ticket" hint="Sur un téléphone, l'appareil photo s'ouvre directement.">
        <input
          type="file"
          accept="image/*,application/pdf"
          capture="environment"
          className="text-sm file:mr-3 file:rounded-md file:border file:border-stone-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium dark:file:border-stone-700 dark:file:bg-stone-900 dark:file:text-stone-100"
          onChange={(e) => {
            setInvalid(null);
            setFile(e.target.files?.[0] ?? null);
          }}
        />
      </Field>
      <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
        <Button type="submit" disabled={send.isPending}>
          {send.isPending ? "Envoi du ticket…" : "Lire le ticket"}
        </Button>
        {send.isPending && <Spinner label="Envoi du ticket…" />}
      </div>
      {(invalid || send.error) && (
        <div className="sm:col-span-2">
          <Alert tone="error">{invalid ?? financeErrorMessage(send.error)}</Alert>
        </div>
      )}
    </form>
  );
}

function ReadValue({ label, value, line }: { label: string; value: string | null; line: string | null }) {
  return (
    <div className="contents">
      <dt className="text-stone-500">{label}</dt>
      <dd className="min-w-0">
        {value ? <span className="font-medium">{value}</span> : <span className="italic text-stone-500">non trouvé</span>}
        {line && (
          <span className="block text-xs break-words text-stone-500">
            Ligne lue : <span className="font-mono">« {line} »</span>
          </span>
        )}
      </dd>
    </div>
  );
}

function AlreadyRecorded({
  ids,
  onShowTransaction,
}: {
  ids: string[];
  onShowTransaction?: (transaction: Transaction) => void;
}) {
  const firstId = ids[0] ?? "";
  const first = useQuery({
    queryKey: financeKeys.transaction(firstId),
    queryFn: () => api<Transaction>(`/v1/finance/transactions/${encodeURIComponent(firstId)}`),
    enabled: firstId !== "",
  });
  const tx = first.data;
  return (
    <span>
      <strong>Ce ticket a déjà été enregistré</strong>
      {ids.length > 1 ? ` (${ids.length} opérations)` : ""}.{" "}
      {tx && (
        <a
          href={`#transaction-${tx.id}`}
          className="font-medium underline underline-offset-2"
          onClick={() => onShowTransaction?.(tx)}
        >
          Voir l&apos;opération du {formatDate(tx.tx_date)} ({signedAmount(tx)})
        </a>
      )}
    </span>
  );
}

/** What was read, line by line, then the prefilled form: nothing is recorded before the owner. */
function Review({ proposal, today, onRecorded, onShowTransaction, onClose }: Callbacks & { proposal: ReceiptProposal }) {
  const recorded = proposal.recorded_transaction_ids;
  const [another, setAnother] = useState(false);
  const currency = proposal.currency;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Ce que LifeOS a lu sur « {proposal.title} »</h3>
        <Badge tone={confidenceTone(proposal.confidence)}>{confidenceLabel(proposal.confidence)}</Badge>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
        <ReadValue label="Commerçant" value={proposal.merchant} line={proposal.lines.merchant} />
        <ReadValue label="Date" value={proposal.tx_date ? formatDate(proposal.tx_date) : null} line={proposal.lines.tx_date} />
        <ReadValue
          label="Total"
          value={proposal.total ? formatMoney(proposal.total, currency) : null}
          line={proposal.lines.total}
        />
        {proposal.vat_rates.length > 0 && (
          <div className="contents">
            <dt className="text-stone-500">TVA</dt>
            <dd>{proposal.vat_rates.map((rate) => `${rate} %`).join(", ")}</dd>
          </div>
        )}
        {proposal.card_payment && (
          <div className="contents">
            <dt className="text-stone-500">Paiement</dt>
            <dd>Carte bancaire</dd>
          </div>
        )}
      </dl>
      {proposal.confidence < LOW_CONFIDENCE && (
        <Alert tone="warning">Lecture peu sûre : vérifiez chaque valeur avant d&apos;ajouter l&apos;opération.</Alert>
      )}
      {proposal.warnings.length > 0 && (
        <Alert tone="warning">
          <ul className="list-inside list-disc">
            {proposal.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
      {recorded.length > 0 && (
        <Alert tone="warning">
          <span className="flex flex-col items-start gap-2">
            <AlreadyRecorded ids={recorded} onShowTransaction={onShowTransaction} />
            {!another && (
              <Button size="sm" variant="secondary" onClick={() => setAnother(true)}>
                Ajouter quand même une autre opération
              </Button>
            )}
          </span>
        </Alert>
      )}
      {(recorded.length === 0 || another) && (
        <section aria-label="Opération à ajouter" className="border-t border-stone-100 pt-3 dark:border-stone-800">
          <h3 className="mb-2 text-sm font-semibold">Vérifiez et corrigez, puis ajoutez l&apos;opération</h3>
          <TransactionForm
            spaceId={proposal.space_id}
            initial={proposalToForm(proposal, today)}
            receipt={{ documentId: proposal.document_id, suggested: proposal.suggested_category }}
            today={today}
            submitLabel="Ajouter l'opération"
            onSaved={onRecorded}
            onCancel={onClose}
          />
        </section>
      )}
    </div>
  );
}

function ManualEntry({ document, today, onRecorded, onClose }: Callbacks & { document: DocumentDetail }) {
  return (
    <section aria-label="Saisie manuelle" className="border-t border-stone-100 pt-3 dark:border-stone-800">
      <h3 className="mb-2 text-sm font-semibold">Saisir l&apos;opération à la main</h3>
      <TransactionForm
        spaceId={document.space_id}
        initial={emptyTransactionForm(today)}
        receipt={{ documentId: document.id }}
        today={today}
        submitLabel="Ajouter l'opération"
        onSaved={onRecorded}
        onCancel={onClose}
      />
    </section>
  );
}

/** Polls the document while it is read, then shows the proposal (or why it failed). */
function ReadingStep({ documentId, pollMs, ...callbacks }: Callbacks & { documentId: string; pollMs: number }) {
  const [manual, setManual] = useState(false);
  const document = useQuery({
    queryKey: financeKeys.document(documentId),
    queryFn: () => api<DocumentDetail>(`/v1/documents/${encodeURIComponent(documentId)}`),
    refetchInterval: (query) => readingPollInterval(query.state.data, pollMs),
  });
  const doc = document.data;
  const ready = doc?.status === "ready" && doc.purpose === "receipt";
  const proposal = useQuery({
    queryKey: financeKeys.proposal(documentId),
    queryFn: () => api<ReceiptProposal>(proposalPath(documentId)),
    enabled: ready,
  });

  if (document.isPending) return <Spinner label="Chargement du ticket…" />;
  if (!doc) {
    return (
      <Alert tone="error">
        {notFound(document.error) ? "Ce ticket est introuvable ou a été supprimé." : financeErrorMessage(document.error)}
      </Alert>
    );
  }

  const manualButton = (label: string): ReactNode =>
    !manual && (
      <Button size="sm" variant="secondary" onClick={() => setManual(true)}>
        {label}
      </Button>
    );
  const failure = receiptFailure(doc);
  if (failure) {
    const receipt = doc.purpose === "receipt";
    return (
      <div className="flex flex-col gap-3">
        <Alert tone="error">
          <span className="flex flex-col items-start gap-2">
            <span>{receipt ? `La lecture du ticket a échoué. ${failure}` : failure}</span>
            {receipt && manualButton("Saisir l'opération à la main")}
          </span>
        </Alert>
        {receipt && manual && <ManualEntry document={doc} {...callbacks} />}
      </div>
    );
  }
  if (isReading(doc)) {
    const delay = readingDelay(doc);
    return (
      <div className="flex flex-col gap-3">
        <Spinner label="Lecture du ticket en cours…" />
        {delay && <Alert tone="info">{delay}</Alert>}
        <div>{manualButton("Saisir à la main sans attendre")}</div>
        {manual && <ManualEntry document={doc} {...callbacks} />}
      </div>
    );
  }
  if (proposal.isPending) return <Spinner label="Lecture du ticket en cours…" />;
  if (!proposal.data) {
    return (
      <div className="flex flex-col items-start gap-2">
        <Alert tone="error">{financeErrorMessage(proposal.error)}</Alert>
        <Button size="sm" variant="secondary" onClick={() => void proposal.refetch()}>
          Réessayer
        </Button>
      </div>
    );
  }
  return <Review proposal={proposal.data} {...callbacks} />;
}

/**
 * UC02: a photo is analysed; the amounts, the date and the merchant are checked before being
 * added to the budget. Opened on an existing receipt (`documentId`) or to scan a new one.
 */
export function ReceiptImport({
  documentId,
  defaultSpaceId,
  pollMs = POLL_MS,
  onUploaded,
  ...callbacks
}: Callbacks & {
  documentId: string | null;
  defaultSpaceId?: string | null;
  pollMs?: number;
  onUploaded?: (documentId: string) => void;
}) {
  const [uploadedId, setUploadedId] = useState<string | null>(null);
  const current = documentId ?? uploadedId;

  return (
    <Card
      title={current ? "Ticket de caisse" : "Scanner un ticket"}
      actions={
        <Button size="sm" variant="ghost" onClick={callbacks.onClose}>
          Fermer
        </Button>
      }
      className="mb-4"
    >
      <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">
        Une photo est analysée ; les montants, la date et le marchand sont vérifiables avant ajout au budget. Rien
        n&apos;est enregistré avant que vous cliquiez sur « Ajouter l&apos;opération ».
      </p>
      {current ? (
        <ReadingStep key={current} documentId={current} pollMs={pollMs} {...callbacks} />
      ) : (
        <UploadStep
          defaultSpaceId={defaultSpaceId}
          onUploaded={(id) => {
            setUploadedId(id);
            onUploaded?.(id);
          }}
        />
      )}
    </Card>
  );
}
