"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ApprovalReview } from "@/components/ApprovalReview";
import { Alert, Badge, Button, Field, Input, Select, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { SENSITIVITY_LABELS } from "@/lib/documents";
import {
  DRAFT_STATE_LABELS,
  type DraftErrors,
  type DraftFields,
  draftErrors,
  formatSize,
  parseAddresses,
  replySubject,
} from "@/lib/mail";
import type { Approval, DocumentSummary, MailDetail, MailDraft, Page, SendAccount } from "@/lib/types";

export const DRAFTS_KEY = "mail-drafts";

const EMPTY: DraftFields = { to: "", cc: "", bcc: "", subject: "", body: "" };

function fieldsOf(draft: MailDraft): DraftFields {
  return {
    to: draft.to.join(", "),
    cc: draft.cc.join(", "),
    bcc: draft.bcc.join(", "),
    subject: draft.subject,
    body: draft.body,
  };
}

function quoted(mail: MailDetail): string {
  const lines = mail.body_text.split("\n").map((line) => `> ${line}`);
  return `\n\n${mail.from_address ?? "L'expéditeur"} a écrit :\n${lines.join("\n")}`;
}

/**
 * Writing an e-mail. Nothing leaves from here: « Préparer l'envoi » saves the draft and
 * asks for an approval that shows the exact message; the owner approves it below (with a
 * passkey for a new recipient or a sensitive attachment). Editing the draft afterwards
 * cancels that approval.
 */
export function MailCompose({
  spaceId,
  draftId,
  replyTo,
  onSaved,
}: {
  spaceId: string;
  draftId?: string | null;
  replyTo?: MailDetail | null;
  onSaved?: (draft: MailDraft) => void;
}) {
  const queryClient = useQueryClient();
  const [currentId, setCurrentId] = useState<string | null>(draftId ?? null);
  const [fields, setFields] = useState<DraftFields>(() =>
    replyTo
      ? { ...EMPTY, to: replyTo.from_address ?? "", subject: replySubject(replyTo.subject), body: quoted(replyTo) }
      : EMPTY,
  );
  const [touched, setTouched] = useState<Partial<Record<keyof DraftFields, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const [accountId, setAccountId] = useState<string>("");
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [loadedVersion, setLoadedVersion] = useState<number | null>(null);

  const accounts = useQuery({
    queryKey: ["mail-send-accounts", spaceId],
    queryFn: () => api<SendAccount[]>(`/v1/mail/send-accounts?space_id=${encodeURIComponent(spaceId)}`),
  });
  const documents = useQuery({
    queryKey: ["documents", "attachable", spaceId],
    queryFn: () =>
      api<Page<DocumentSummary>>(`/v1/documents?space_id=${encodeURIComponent(spaceId)}&status=ready&limit=200`),
  });
  const draft = useQuery({
    queryKey: [DRAFTS_KEY, "detail", currentId],
    queryFn: () => api<MailDraft>(`/v1/mail/drafts/${currentId}`),
    enabled: currentId !== null,
  });
  const approval = useQuery({
    queryKey: ["approvals", "draft", currentId, draft.data?.approval_id],
    queryFn: () => api<Approval>(`/v1/mail/drafts/${currentId}/approval`),
    enabled: currentId !== null && Boolean(draft.data?.approval_id) && draft.data?.state !== "draft",
  });

  // The draft as saved, once loaded (or reloaded after a new version).
  useEffect(() => {
    const data = draft.data;
    if (!data || data.version === loadedVersion) return;
    setFields(fieldsOf(data));
    setAccountId(data.account_id ?? "");
    setAttachmentIds(data.attachments.map((a) => a.document_id));
    setLoadedVersion(data.version);
  }, [draft.data, loadedVersion]);

  const refresh = (saved: MailDraft) => {
    queryClient.setQueryData([DRAFTS_KEY, "detail", saved.id], saved);
    void queryClient.invalidateQueries({ queryKey: [DRAFTS_KEY, "list"] });
    onSaved?.(saved);
  };

  const body = () => ({
    account_id: accountId || null,
    to: parseAddresses(fields.to),
    cc: parseAddresses(fields.cc),
    bcc: parseAddresses(fields.bcc),
    subject: fields.subject.trim(),
    body: fields.body,
    attachment_ids: attachmentIds,
  });

  const persist = async (): Promise<MailDraft> => {
    if (currentId === null) {
      const created = await api<MailDraft>("/v1/mail/drafts", {
        method: "POST",
        idempotent: true,
        body: { space_id: spaceId, in_reply_to_id: replyTo?.id ?? null, ...body() },
      });
      setCurrentId(created.id);
      setLoadedVersion(created.version);
      return created;
    }
    const updated = await api<MailDraft>(`/v1/mail/drafts/${currentId}`, {
      method: "PATCH",
      body: body(),
      headers: draft.data ? { "If-Match": String(draft.data.version) } : undefined,
    });
    setLoadedVersion(updated.version);
    return updated;
  };

  const save = useMutation({ mutationFn: persist, onSuccess: refresh });
  const prepare = useMutation({
    mutationFn: async () => {
      const saved = await persist();
      const proposal = await api<Approval>(`/v1/mail/drafts/${saved.id}/send`, { method: "POST", idempotent: true });
      const fresh = await api<MailDraft>(`/v1/mail/drafts/${saved.id}`);
      return { draft: fresh, proposal };
    },
    onSuccess: ({ draft: fresh, proposal }) => {
      refresh(fresh);
      queryClient.setQueryData(["approvals", "draft", fresh.id, proposal.id], proposal);
    },
  });
  const resolve = useMutation({
    mutationFn: (sent: boolean) =>
      api<MailDraft>(`/v1/mail/drafts/${currentId}/resolve`, { method: "POST", body: { sent } }),
    onSuccess: refresh,
  });

  const errors: DraftErrors = draftErrors(fields, { sending: submitted });
  const shown = (key: keyof DraftFields) => (touched[key] || submitted ? errors[key] : undefined);
  const set = (key: keyof DraftFields) => (value: string) => setFields((f) => ({ ...f, [key]: value }));
  const blur = (key: keyof DraftFields) => () => setTouched((t) => ({ ...t, [key]: true }));

  if (accounts.isPending || (currentId !== null && draft.isPending)) return <Spinner />;
  if (accounts.error) return <Alert tone="error">{errorMessage(accounts.error)}</Alert>;
  if (draft.error) return <Alert tone="error">{errorMessage(draft.error)}</Alert>;
  if (accounts.data.length === 0) {
    return (
      <Alert tone="warning">
        Aucun compte connecté ne peut envoyer d&apos;e-mails dans cet espace. Autorisez « Envoyer des e-mails » dans{" "}
        <Link href="/settings" className="underline">
          Réglages &gt; Comptes connectés
        </Link>
        .
      </Alert>
    );
  }

  const state = draft.data?.state ?? "draft";
  const locked = state === "sent" || state === "sending" || state === "unknown";
  const busy = save.isPending || prepare.isPending;
  const attachable = (documents.data?.items ?? []).filter((d) => d.original_downloadable);

  const submit = (sending: boolean) => {
    setSubmitted(true);
    if (Object.keys(draftErrors(fields, { sending })).length > 0) return;
    if (sending) prepare.mutate();
    else save.mutate();
  };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {draft.data && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={state === "sent" ? "teal" : state === "unknown" || state === "failed" ? "amber" : "neutral"}>
            {DRAFT_STATE_LABELS[state] ?? state}
          </Badge>
          {draft.data.reply && <span className="text-muted">Réponse dans la même conversation</span>}
        </p>
      )}
      {state === "unknown" && (
        <Alert tone="warning">
          <p>
            État incertain — vérifiez vos e-mails envoyés. LifeOS ne renvoie jamais cet e-mail de lui-même. Une fois
            vérifié, indiquez-le :
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => resolve.mutate(true)} disabled={resolve.isPending}>
              Il est bien parti
            </Button>
            <Button size="sm" variant="secondary" onClick={() => resolve.mutate(false)} disabled={resolve.isPending}>
              Il n&apos;est pas parti
            </Button>
          </div>
        </Alert>
      )}
      {resolve.error && <Alert tone="error">{errorMessage(resolve.error)}</Alert>}
      <form
        className="flex flex-col gap-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit(true);
        }}
      >
        <fieldset disabled={locked || busy} className="flex flex-col gap-3">
          <Field label="Compte d'envoi">
            <Select value={accountId || accounts.data[0]?.id || ""} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.data.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.email ?? a.provider}
                </option>
              ))}
            </Select>
          </Field>
          {(["to", "cc", "bcc"] as const).map((key) => (
            <Field
              key={key}
              label={key === "to" ? "À" : key === "cc" ? "Cc" : "Cci (copie cachée)"}
              hint={shown(key) ?? (key === "to" ? "Séparez les adresses par des virgules." : undefined)}
            >
              <Input
                type="text"
                inputMode="email"
                autoComplete="off"
                value={fields[key]}
                aria-invalid={shown(key) ? true : undefined}
                onChange={(e) => set(key)(e.target.value)}
                onBlur={blur(key)}
              />
            </Field>
          ))}
          <Field label="Objet" hint={shown("subject")}>
            <Input
              type="text"
              value={fields.subject}
              maxLength={300}
              aria-invalid={shown("subject") ? true : undefined}
              onChange={(e) => set("subject")(e.target.value)}
              onBlur={blur("subject")}
            />
          </Field>
          <Field label="Message" hint={shown("body") ?? "Texte brut : il part tel que vous l'écrivez."}>
            <Textarea
              rows={10}
              value={fields.body}
              aria-invalid={shown("body") ? true : undefined}
              onChange={(e) => set("body")(e.target.value)}
              onBlur={blur("body")}
            />
          </Field>
          <fieldset className="flex flex-col gap-1 text-sm">
            <legend className="font-medium text-stone-700 dark:text-stone-300">Pièces jointes</legend>
            {documents.isPending ? (
              <Spinner label="Documents…" />
            ) : attachable.length === 0 ? (
              <p className="text-xs text-muted">Aucun document de cet espace ne peut être joint.</p>
            ) : (
              attachable.map((d) => (
                <label key={d.id} className="flex flex-wrap items-center gap-2 py-0.5">
                  <input
                    type="checkbox"
                    className="accent-teal-700"
                    checked={attachmentIds.includes(d.id)}
                    onChange={(e) =>
                      setAttachmentIds((ids) => (e.target.checked ? [...ids, d.id] : ids.filter((i) => i !== d.id)))
                    }
                  />
                  <span className="break-all">{d.title}</span>
                  <span className="text-xs text-muted">{formatSize(d.size_bytes)}</span>
                  {d.sensitivity.map((s) => (
                    <Badge key={s} tone="violet">
                      {SENSITIVITY_LABELS[s] ?? s}
                    </Badge>
                  ))}
                  {d.local_only && <Badge tone="violet">Gardé en local</Badge>}
                </label>
              ))
            )}
          </fieldset>
        </fieldset>
        {(save.error || prepare.error) && <Alert tone="error">{errorMessage(save.error ?? prepare.error)}</Alert>}
        {!locked && (
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              Préparer l&apos;envoi
            </Button>
            <Button type="button" variant="secondary" disabled={busy} onClick={() => submit(false)}>
              Enregistrer le brouillon
            </Button>
          </div>
        )}
        {state === "awaiting_approval" && (
          <p className="text-xs text-muted">Modifier le brouillon annule l&apos;approbation en attente.</p>
        )}
      </form>
      {approval.data && state !== "draft" && (
        <section aria-label="Approbation de l'envoi" className="flex flex-col gap-2">
          <h2 className="text-base font-semibold">Vérifiez l&apos;e-mail avant de l&apos;envoyer</h2>
          <ApprovalReview
            key={`${approval.data.id}-${approval.data.status}`}
            approval={approval.data}
            onDone={() => {
              void queryClient.invalidateQueries({ queryKey: [DRAFTS_KEY] });
              void queryClient.invalidateQueries({ queryKey: ["approvals", "draft", currentId] });
            }}
          />
        </section>
      )}
    </div>
  );
}
