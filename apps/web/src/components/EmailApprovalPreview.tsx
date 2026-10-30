import { Badge } from "@/components/ui";
import { SENSITIVITY_LABELS } from "@/lib/documents";
import { formatSize } from "@/lib/mail";
import type { EmailPreview, SensitivityCategory } from "@/lib/types";

export const EMAIL_ACTIONS = new Set(["email.send", "email.send_reinforced"]);

function AddressList({ label, addresses, fresh }: { label: string; addresses: string[]; fresh: Set<string> }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0">
        <ul aria-label={label} className="flex flex-col">
          {addresses.map((a) => (
            <li key={a} className="flex flex-wrap items-center gap-1 break-all">
              {a}
              {fresh.has(a) && <Badge tone="amber">Nouveau destinataire</Badge>}
            </li>
          ))}
        </ul>
      </dd>
    </>
  );
}

/**
 * The exact e-mail an approval sends: sender account, To, Cc, Bcc, subject, the whole
 * plain-text body and the attachments. Rendered as plain text, never as markup (T14).
 */
export function EmailApprovalPreview({ preview }: { preview: EmailPreview }) {
  if (preview.redacted) {
    return (
      <p className="text-sm text-stone-600 dark:text-stone-400">
        {preview.summary_line}
        {preview.recipients !== undefined && ` (${preview.recipients} destinataire${preview.recipients > 1 ? "s" : ""})`}.
      </p>
    );
  }
  const fresh = new Set(preview.new_recipients ?? []);
  const to = preview.to ?? [];
  const cc = preview.cc ?? [];
  const bcc = preview.bcc ?? [];
  const attachments = preview.attachments ?? [];
  return (
    <div className="flex min-w-0 flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="violet">{preview.reply ? "Réponse" : "Nouvel e-mail"}</Badge>
        {preview.reinforced && <Badge tone="amber">Confirmation par passkey</Badge>}
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        <dt className="text-muted">De</dt>
        <dd className="break-all">{preview.from}</dd>
        <AddressList label="À" addresses={to} fresh={fresh} />
        {cc.length > 0 && <AddressList label="Cc" addresses={cc} fresh={fresh} />}
        {bcc.length > 0 && <AddressList label="Cci" addresses={bcc} fresh={fresh} />}
        <dt className="text-muted">Objet</dt>
        <dd className="break-words font-medium">{preview.subject || "(sans objet)"}</dd>
      </dl>
      <pre
        aria-label="Texte de l'e-mail"
        className="max-h-80 overflow-y-auto rounded-md border border-stone-200 bg-stone-50 p-3 font-sans text-sm break-words whitespace-pre-wrap dark:border-stone-800 dark:bg-stone-950"
      >
        {preview.body || "(message sans texte)"}
      </pre>
      {attachments.length > 0 && (
        <div>
          <p className="font-medium">Pièces jointes</p>
          <ul aria-label="Pièces jointes" className="flex flex-col gap-1">
            {attachments.map((a) => (
              <li key={`${a.filename}-${a.size_bytes}`} className="flex flex-wrap items-center gap-1.5">
                <span className="break-all">{a.filename}</span>
                <span className="text-xs text-muted">{formatSize(a.size_bytes)}</span>
                {a.sensitivity.map((s) => (
                  <Badge key={s} tone="violet">
                    {SENSITIVITY_LABELS[s as SensitivityCategory] ?? s}
                  </Badge>
                ))}
                {a.local_only && <Badge tone="violet">Gardé en local</Badge>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(preview.notes ?? []).length > 0 && (
        <ul className="ml-4 list-disc text-xs text-stone-600 dark:text-stone-400">
          {(preview.notes ?? []).map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
