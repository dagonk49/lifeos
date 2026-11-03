"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { DRAFTS_KEY, MailCompose } from "@/components/MailCompose";
import { Alert, Badge, Card, Empty, PageHeader, Spinner, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { DRAFT_STATE_LABELS, MAIL_KEY, subjectLabel } from "@/lib/mail";
import { spaceParam, useSpace } from "@/lib/space";
import type { MailDetail, MailDraft } from "@/lib/types";

function Drafts({ selectedId }: { selectedId: string | null }) {
  const { selected } = useSpace();
  const drafts = useQuery({
    queryKey: [DRAFTS_KEY, "list", selected],
    queryFn: () => api<MailDraft[]>(`/v1/mail/drafts${spaceParam(selected)}`),
  });
  if (drafts.isPending) return <Spinner />;
  if (drafts.error) return <Alert tone="error">{errorMessage(drafts.error)}</Alert>;
  if (drafts.data.length === 0) return <Empty>Aucun brouillon pour l&apos;instant.</Empty>;
  return (
    <ul aria-label="Brouillons et envois" className="flex flex-col divide-y divide-stone-200 dark:divide-stone-800">
      {drafts.data.map((d) => (
        <li key={d.id}>
          <Link
            href={`/mail/compose?draft=${encodeURIComponent(d.id)}`}
            aria-current={d.id === selectedId ? "true" : undefined}
            className={cx(
              "flex min-w-0 flex-col gap-0.5 rounded px-2 py-2 text-sm hover:bg-stone-100 dark:hover:bg-stone-800",
              d.id === selectedId && "bg-teal-50 dark:bg-teal-950",
            )}
          >
            <span className="truncate font-medium">{subjectLabel(d.subject)}</span>
            <span className="truncate text-xs text-muted">
              {d.to.join(", ") || "Sans destinataire"} · {formatDateTime(d.updated_at)}
            </span>
            <span>
              <Badge tone={d.state === "sent" ? "teal" : d.state === "unknown" || d.state === "failed" ? "amber" : "neutral"}>
                {DRAFT_STATE_LABELS[d.state] ?? d.state}
              </Badge>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function ComposeView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { writeSpaceId } = useSpace();
  const draftId = searchParams.get("draft");
  const replyId = searchParams.get("reply");

  const draft = useQuery({
    queryKey: [DRAFTS_KEY, "detail", draftId],
    queryFn: () => api<MailDraft>(`/v1/mail/drafts/${draftId}`),
    enabled: draftId !== null,
  });
  const reply = useQuery({
    queryKey: [MAIL_KEY, "detail", replyId],
    queryFn: () => api<MailDetail>(`/v1/mail/messages/${replyId}`),
    enabled: replyId !== null && draftId === null,
  });

  let form: ReactNode;
  if ((draftId !== null && draft.isPending) || (replyId !== null && draftId === null && reply.isPending)) {
    form = <Spinner />;
  } else if (draft.error || reply.error) {
    form = <Alert tone="error">{errorMessage(draft.error ?? reply.error)}</Alert>;
  } else {
    const spaceId = draft.data?.space_id ?? reply.data?.space_id ?? writeSpaceId;
    form = (
      <MailCompose
        key={draftId ?? `new-${replyId ?? ""}`}
        spaceId={spaceId}
        draftId={draftId}
        replyTo={draftId === null ? (reply.data ?? null) : null}
        onSaved={(saved) => {
          if (saved.id !== draftId) router.replace(`${pathname}?draft=${encodeURIComponent(saved.id)}`, { scroll: false });
        }}
      />
    );
  }

  return (
    <>
      <PageHeader title="Écrire un e-mail">
        <Link href="/mail" className="text-sm text-teal-700 underline dark:text-teal-400">
          Retour aux e-mails
        </Link>
      </PageHeader>
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]">
        <Card
          footer="Rien ne part sans votre approbation : l'aperçu montre exactement le message, et un nouveau destinataire ou une pièce jointe sensible demande votre passkey."
        >
          {form}
        </Card>
        <Card title="Brouillons et envois" className="max-h-[75vh] overflow-y-auto">
          <Drafts selectedId={draftId} />
        </Card>
      </div>
    </>
  );
}

export default function ComposePage() {
  return (
    <Suspense fallback={<Spinner />}>
      <ComposeView />
    </Suspense>
  );
}
