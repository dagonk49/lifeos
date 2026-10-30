"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Spinner } from "@/components/ui";
import { api, newIdempotencyKey, upload } from "@/lib/api";
import {
  countLabel,
  financeErrorMessage,
  financeKeys,
  formatMoney,
  idempotencyKeyFor,
  signedAmount,
  TRANSACTION_CHANGE_KEYS,
  type PendingKey,
} from "@/lib/finance";
import {
  buildMapping,
  commitBody,
  countsSummary,
  DATE_FORMAT_LABELS,
  DATE_FORMATS,
  ENCODING_LABELS,
  IMPORT_ACCEPT,
  IMPORT_STATUS_LABELS,
  importButtonLabel,
  importPath,
  importRowsPath,
  importsListPath,
  isSelectable,
  mappingToForm,
  reasonLabel,
  resultText,
  ROW_STATUS_LABELS,
  ROW_STATUS_TONES,
  selectedByDefault,
  selectionTotals,
  totalsText,
  type MappingForm,
} from "@/lib/financeImport";
import { formatDate } from "@/lib/format";
import { useSpace } from "@/lib/space";
import type { FinanceImport as Import, ImportRow, Page } from "@/lib/types";

// --- Step 1: the file -----------------------------------------------------------------------

function UploadStep({ defaultSpaceId, onCreated }: { defaultSpaceId?: string | null; onCreated: (id: string) => void }) {
  const queryClient = useQueryClient();
  const { spaces, selected } = useSpace();
  const [space, setSpace] = useState(() => selected ?? defaultSpaceId ?? (spaces.length === 1 ? (spaces[0]?.id ?? "") : ""));
  const [file, setFile] = useState<File | null>(null);
  const [account, setAccount] = useState("");
  const [encoding, setEncoding] = useState("auto");
  const [invalid, setInvalid] = useState<string | null>(null);

  const send = useMutation({
    mutationFn: ({ picked, spaceId }: { picked: File; spaceId: string }) => {
      const form = new FormData();
      form.append("space_id", spaceId);
      if (account.trim()) form.append("account_label", account.trim());
      form.append("encoding", encoding);
      form.append("file", picked);
      return upload<Import>(importPath(), form, { idempotent: true });
    },
    onSuccess: (created) => {
      queryClient.setQueryData(financeKeys.import(created.id), created);
      void queryClient.invalidateQueries({ queryKey: financeKeys.importsAll });
    },
  });

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const spaceId = selected ?? space;
        if (!spaceId) return setInvalid("Choisissez l'espace du relevé.");
        if (!file) return setInvalid("Choisissez le fichier CSV exporté depuis votre banque.");
        send.mutate({ picked: file, spaceId }, { onSuccess: (created) => onCreated(created.id) });
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
      <Field label="Relevé CSV" hint="Le fichier n'est pas conservé : seules les opérations validées sont enregistrées.">
        <input
          type="file"
          accept={IMPORT_ACCEPT}
          className="text-sm file:mr-3 file:rounded-md file:border file:border-stone-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium dark:file:border-stone-700 dark:file:bg-stone-900 dark:file:text-stone-100"
          onChange={(e) => {
            setInvalid(null);
            setFile(e.target.files?.[0] ?? null);
          }}
        />
      </Field>
      <Field label="Compte (facultatif)" hint="Pour distinguer deux comptes importés dans le même espace.">
        <Input value={account} maxLength={60} onChange={(e) => setAccount(e.target.value)} autoComplete="off" />
      </Field>
      <Field label="Encodage" hint="Automatique : UTF-8 si possible, sinon Windows-1252.">
        <Select value={encoding} onChange={(e) => setEncoding(e.target.value)}>
          <option value="auto">Automatique</option>
          <option value="utf-8">{ENCODING_LABELS["utf-8"]}</option>
          <option value="cp1252">{ENCODING_LABELS.cp1252}</option>
        </Select>
      </Field>
      <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
        <Button type="submit" disabled={send.isPending}>
          {send.isPending ? "Lecture du fichier…" : "Lire le fichier"}
        </Button>
      </div>
      {(invalid || send.error) && (
        <div className="sm:col-span-2">
          <Alert tone="error">{invalid ?? financeErrorMessage(send.error)}</Alert>
        </div>
      )}
    </form>
  );
}

// --- Step 2: the columns -------------------------------------------------------------------

function ColumnSelect({
  label,
  value,
  header,
  optional,
  onChange,
}: {
  label: string;
  value: string;
  header: string[];
  optional?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{optional ? "Aucune" : "Choisissez…"}</option>
        {header.map((name, index) => (
          <option key={`${index}-${name}`} value={String(index)}>
            {name}
          </option>
        ))}
      </Select>
    </Field>
  );
}

function MappingStep({ current, onMapped }: { current: Import; onMapped: (updated: Import) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<MappingForm>(() => mappingToForm(current.mapping ?? current.suggested_mapping));
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = (patch: Partial<MappingForm>) => {
    setInvalid(null);
    setForm((f) => ({ ...f, ...patch }));
  };
  const save = useMutation({
    mutationFn: (body: object) => api<Import>(`${importPath(current.id)}/mapping`, { method: "PUT", body }),
    onSuccess: (updated) => {
      queryClient.setQueryData(financeKeys.import(updated.id), updated);
      void queryClient.invalidateQueries({ queryKey: financeKeys.importRows(updated.id) });
      onMapped(updated);
    },
  });

  return (
    <section aria-label="Colonnes du fichier" className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">2. Colonnes</h3>
      <div role="region" aria-label="Aperçu du fichier" tabIndex={0} className="overflow-x-auto">
        <table className="min-w-full text-left text-xs">
          <caption className="mb-1 text-left text-muted">
            Premières lignes telles que lues ({ENCODING_LABELS[current.encoding]}, séparateur « {current.delimiter === "\t" ? "tabulation" : current.delimiter} »)
          </caption>
          <thead>
            <tr>
              {current.header.map((name, index) => (
                <th key={`${index}-${name}`} scope="col" className="border-b border-stone-200 px-2 py-1 font-medium dark:border-stone-700">
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {current.sample.map((cells, row) => (
              <tr key={row}>
                {cells.map((cell, index) => (
                  <td key={index} className="px-2 py-1 font-mono whitespace-nowrap">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form
        className="grid gap-3 sm:grid-cols-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const result = buildMapping(form, current.header.length);
          if (!result.ok) return setInvalid(result.error);
          save.mutate(result.value);
        }}
      >
        <ColumnSelect label="Date" value={form.date_column} header={current.header} onChange={(v) => set({ date_column: v })} />
        <Field label="Format des dates">
          <Select value={form.date_format} onChange={(e) => set({ date_format: e.target.value as MappingForm["date_format"] })}>
            {DATE_FORMATS.map((fmt) => (
              <option key={fmt} value={fmt}>
                {DATE_FORMAT_LABELS[fmt]}
              </option>
            ))}
          </Select>
        </Field>
        <ColumnSelect label="Libellé" value={form.label_column} header={current.header} onChange={(v) => set({ label_column: v })} />
        <fieldset className="flex flex-col gap-1 text-sm sm:col-span-3">
          <legend className="mb-1 font-medium text-stone-700 dark:text-stone-300">Montants</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="amount-mode"
                className="accent-teal-700"
                checked={form.amount_mode === "signed"}
                onChange={() => set({ amount_mode: "signed" })}
              />
              Une colonne signée (−12,50 pour une dépense)
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="amount-mode"
                className="accent-teal-700"
                checked={form.amount_mode === "debit_credit"}
                onChange={() => set({ amount_mode: "debit_credit" })}
              />
              Deux colonnes : débit et crédit
            </label>
          </div>
        </fieldset>
        {form.amount_mode === "signed" ? (
          <ColumnSelect label="Montant" value={form.amount_column} header={current.header} onChange={(v) => set({ amount_column: v })} />
        ) : (
          <>
            <ColumnSelect label="Débit" value={form.debit_column} header={current.header} onChange={(v) => set({ debit_column: v })} />
            <ColumnSelect label="Crédit" value={form.credit_column} header={current.header} onChange={(v) => set({ credit_column: v })} />
          </>
        )}
        <Field label="Séparateur décimal">
          <Select
            value={form.decimal_separator}
            onChange={(e) => set({ decimal_separator: e.target.value as MappingForm["decimal_separator"] })}
          >
            <option value=",">Virgule (1 234,56)</option>
            <option value=".">Point (1,234.56)</option>
          </Select>
        </Field>
        <ColumnSelect
          label="Référence (facultatif)"
          value={form.reference_column}
          header={current.header}
          optional
          onChange={(v) => set({ reference_column: v })}
        />
        <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
          <Button type="submit" variant="secondary" disabled={save.isPending}>
            {save.isPending ? "Relecture…" : "Relire avec ces colonnes"}
          </Button>
        </div>
        {(invalid || save.error) && (
          <div className="sm:col-span-3">
            <Alert tone="error">{invalid ?? financeErrorMessage(save.error)}</Alert>
          </div>
        )}
      </form>
    </section>
  );
}

// --- Step 3: the check ---------------------------------------------------------------------

function RowLine({ row, checked, onToggle }: { row: ImportRow; checked: boolean; onToggle: () => void }) {
  const selectable = isSelectable(row);
  const id = useId();
  const amount =
    row.amount && row.direction && row.currency
      ? signedAmount({ amount: row.amount, direction: row.direction, currency: row.currency })
      : null;
  return (
    <li className="flex items-start gap-2 border-b border-stone-100 py-1.5 text-sm last:border-0 dark:border-stone-800">
      {selectable ? (
        <input id={id} type="checkbox" className="mt-1 accent-teal-700" checked={checked} onChange={onToggle} />
      ) : (
        <span className="mt-1 inline-block w-3.5" aria-hidden />
      )}
      <label htmlFor={selectable ? id : undefined} className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-xs text-muted">Ligne {row.line_no}</span>
        <Badge tone={ROW_STATUS_TONES[row.status]}>{ROW_STATUS_LABELS[row.status]}</Badge>
        {row.tx_date && <span>{formatDate(row.tx_date)}</span>}
        {/* Bank labels are untrusted text: shown as plain text, never as markup. */}
        <span className="min-w-0 break-words">{row.label ?? row.cells.join(" · ")}</span>
        {amount && <span className="ml-auto font-medium tabular-nums">{amount}</span>}
        {row.reason && <span className="w-full text-xs text-red-700 dark:text-red-400">{reasonLabel(row.reason)}</span>}
        {row.status === "possible_duplicate" && row.match_date && (
          <span className="w-full text-xs text-amber-800 dark:text-amber-300">
            Ressemble à une opération déjà saisie le {formatDate(row.match_date)} (même montant) : cochez-la pour
            l&apos;importer quand même.
          </span>
        )}
      </label>
    </li>
  );
}

function ReviewStep({ current, onCommitted, onDiscarded }: { current: Import; onCommitted: (done: Import) => void; onDiscarded: () => void }) {
  const queryClient = useQueryClient();
  const rows = useQuery({
    queryKey: [...financeKeys.importRows(current.id), current.version],
    queryFn: () => api<Page<ImportRow>>(importRowsPath(current.id)),
  });
  const items = useMemo(() => rows.data?.items ?? [], [rows.data]);
  const [overrides, setOverrides] = useState<Map<number, boolean>>(new Map());
  const selected = useMemo(() => {
    const chosen = new Set<number>();
    for (const row of items) {
      if (!isSelectable(row)) continue;
      if (overrides.get(row.line_no) ?? selectedByDefault(row)) chosen.add(row.line_no);
    }
    return chosen;
  }, [items, overrides]);
  const [pending, setPending] = useState<PendingKey | null>(null);
  const totals = selectionTotals(items, selected);
  const unseen = Math.max(0, (current.counts.valid ?? 0) - items.filter((r) => r.status === "valid").length);
  const count = totals.count + unseen;

  const commit = useMutation({
    mutationFn: ({ body, key }: { body: object; key: string }) =>
      api<Import>(`${importPath(current.id)}/commit`, { method: "POST", body, headers: { "Idempotency-Key": key } }),
    onSuccess: (done) => {
      setPending(null);
      queryClient.setQueryData(financeKeys.import(done.id), done);
      for (const queryKey of TRANSACTION_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
      onCommitted(done);
    },
  });
  const discard = useMutation({
    mutationFn: () => api(importPath(current.id), { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: financeKeys.importsAll });
      onDiscarded();
    },
  });

  return (
    <section aria-label="Vérification" className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">3. Vérification</h3>
      {current.already_imported_file && (
        <Alert tone="warning">Ce fichier a déjà été importé dans cet espace : ses opérations connues ne seront pas ajoutées deux fois.</Alert>
      )}
      <ul aria-label="Lignes par état" className="flex flex-wrap gap-2 text-sm">
        {countsSummary(current.counts).map((entry) => (
          <li key={entry.status}>
            <Badge tone={ROW_STATUS_TONES[entry.status]}>
              {entry.label} : {entry.count}
            </Badge>
          </li>
        ))}
      </ul>
      {rows.isPending ? (
        <Spinner label="Chargement des lignes…" />
      ) : rows.error ? (
        <Alert tone="error">{financeErrorMessage(rows.error)}</Alert>
      ) : items.length === 0 ? (
        <Empty>Aucune ligne à afficher.</Empty>
      ) : (
        <ul aria-label="Lignes du relevé" className="max-h-96 overflow-y-auto">
          {items.map((row) => (
            <RowLine
              key={row.line_no}
              row={row}
              checked={selected.has(row.line_no)}
              onToggle={() =>
                setOverrides((map) => new Map(map).set(row.line_no, !selected.has(row.line_no)))
              }
            />
          ))}
        </ul>
      )}
      {rows.data?.next_cursor && (
        <p className="text-xs text-muted">
          Seules les {items.length} premières lignes sont affichées ; les suivantes suivent le choix par défaut (nouvelles
          opérations importées, doublons possibles écartés).
        </p>
      )}
      <p className="text-sm" aria-live="polite">
        {totalsText(totals)}
        {unseen > 0 ? ` (plus ${countLabel(unseen, "ligne non affichée", "lignes non affichées")})` : ""}.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={count === 0 || commit.isPending}
          onClick={() => {
            const body = commitBody(items, selected);
            const next = idempotencyKeyFor(pending, body, newIdempotencyKey);
            setPending(next);
            commit.mutate({ body, key: next.key });
          }}
        >
          {commit.isPending ? "Import en cours…" : importButtonLabel(count)}
        </Button>
        <Button variant="secondary" disabled={discard.isPending} onClick={() => discard.mutate()}>
          Abandonner cet import
        </Button>
      </div>
      {(commit.error || discard.error) && <Alert tone="error">{financeErrorMessage(commit.error ?? discard.error)}</Alert>}
    </section>
  );
}

// --- Step 4: the result, and the undo ----------------------------------------------------------

function UndoConfirm({ current, onDone, onCancel }: { current: Import; onDone: (undone: Import) => void; onCancel: () => void }) {
  const queryClient = useQueryClient();
  const undo = useMutation({
    mutationFn: () => api<Import>(`${importPath(current.id)}/undo`, { method: "POST", body: { confirm: true } }),
    onSuccess: (undone) => {
      queryClient.setQueryData(financeKeys.import(undone.id), undone);
      for (const queryKey of TRANSACTION_CHANGE_KEYS) void queryClient.invalidateQueries({ queryKey });
      onDone(undone);
    },
  });
  const name = current.filename ?? "relevé";
  const question = `Annuler l'import « ${name} » ?`;
  return (
    <div role="group" aria-label={question} className="flex flex-col gap-2 rounded-md border border-stone-300 p-3 text-sm dark:border-stone-700">
      <p className="font-medium">{question}</p>
      <p className="text-stone-600 dark:text-stone-400">
        Les {countLabel(current.imported_count, "opération ajoutée", "opérations ajoutées")} par cet import seront supprimées, y
        compris celles que vous avez corrigées depuis (catégorie, notes). Les autres opérations ne changent pas.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="danger" disabled={undo.isPending} onClick={() => undo.mutate()}>
          {undo.isPending ? "Suppression…" : `Supprimer les opérations de « ${name} »`}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Garder l&apos;import
        </Button>
      </div>
      {undo.error && <Alert tone="error">{financeErrorMessage(undo.error)}</Alert>}
    </div>
  );
}

function ResultStep({ current }: { current: Import }) {
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  if (current.status === "undone") {
    return (
      <Alert tone="info">
        {notice ?? "Cet import a été annulé : ses opérations ont été supprimées."}
        {current.edited_count > 0 &&
          ` ${countLabel(current.edited_count, "opération corrigée a", "opérations corrigées ont")} été supprimée(s) avec.`}
      </Alert>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <Alert tone="success">{resultText(current)}</Alert>
      {current.imported_count > 0 &&
        (confirming ? (
          <UndoConfirm
            current={current}
            onDone={() => {
              setConfirming(false);
              setNotice("Import annulé : ses opérations ont été supprimées.");
            }}
            onCancel={() => setConfirming(false)}
          />
        ) : (
          <div>
            <Button variant="secondary" size="sm" onClick={() => setConfirming(true)}>
              Annuler cet import
            </Button>
          </div>
        ))}
    </div>
  );
}

function ImportView({ importId, onClose }: { importId: string; onClose: () => void }) {
  const query = useQuery({
    queryKey: financeKeys.import(importId),
    queryFn: () => api<Import>(importPath(importId)),
  });
  const current = query.data;
  if (query.isPending) return <Spinner label="Chargement de l'import…" />;
  if (!current) return <Alert tone="error">{financeErrorMessage(query.error)}</Alert>;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-stone-600 dark:text-stone-400">
        <span className="font-medium">{current.filename ?? "Relevé"}</span>
        {current.account_label ? ` · ${current.account_label}` : ""} · {countLabel(current.row_count, "ligne", "lignes")} ·{" "}
        {IMPORT_STATUS_LABELS[current.status]}
      </p>
      {current.status === "preview" ? (
        <>
          <MappingStep key={current.version} current={current} onMapped={() => undefined} />
          {current.mapping ? (
            <ReviewStep key={current.version} current={current} onCommitted={() => undefined} onDiscarded={onClose} />
          ) : (
            <Alert tone="info">Choisissez les colonnes du fichier, puis relisez-le.</Alert>
          )}
        </>
      ) : (
        <ResultStep current={current} />
      )}
    </div>
  );
}

/**
 * Task 25: a CSV statement from the owner's bank, read into a preview; nothing is recorded
 * before « Importer », the same operation is never imported twice, an import can be undone.
 */
export function FinanceImport({
  importId,
  defaultSpaceId,
  onCreated,
  onClose,
}: {
  importId: string | null;
  defaultSpaceId?: string | null;
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <Card
      title={importId ? "Import d'un relevé" : "Importer un relevé (CSV)"}
      actions={
        <Button size="sm" variant="ghost" onClick={onClose}>
          Fermer
        </Button>
      }
      className="mb-4"
    >
      <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">
        Exportez vos opérations en CSV depuis l&apos;espace en ligne de votre banque. Aucune connexion bancaire : LifeOS lit
        le fichier, vous vérifiez, et rien n&apos;est enregistré avant « Importer ».
      </p>
      {importId ? (
        <ImportView key={importId} importId={importId} onClose={onClose} />
      ) : (
        <>
          <h3 className="mb-2 text-sm font-semibold">1. Fichier</h3>
          <UploadStep defaultSpaceId={defaultSpaceId} onCreated={onCreated} />
        </>
      )}
    </Card>
  );
}

/** Past imports of the space, to reopen one (and undo it). */
export function ImportHistory({ spaceId, onOpen }: { spaceId: string; onOpen: (id: string) => void }) {
  const history = useQuery({
    queryKey: financeKeys.imports(spaceId),
    queryFn: () => api<Import[]>(importsListPath(spaceId)),
  });
  const items = (history.data ?? []).filter((i) => i.status !== "preview" || new Date(i.expires_at) > new Date());
  if (history.isPending || items.length === 0) return null;
  return (
    <Card title="Imports de relevés">
      <ul className="flex flex-col gap-2 text-sm">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-2">
            <span className="min-w-0">
              <span className="font-medium break-words">{item.filename ?? "Relevé"}</span>{" "}
              <span className="text-muted">
                · {formatDate(item.created_at.slice(0, 10))} · {IMPORT_STATUS_LABELS[item.status]}
                {item.status === "committed" ? ` · ${countLabel(item.imported_count, "opération", "opérations")}` : ""}
                {item.totals ? ` · ${formatMoney(item.totals.expenses)} de dépenses` : ""}
              </span>
            </span>
            <Button size="sm" variant="ghost" onClick={() => onOpen(item.id)}>
              {item.status === "preview" ? "Reprendre" : "Voir"}
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
