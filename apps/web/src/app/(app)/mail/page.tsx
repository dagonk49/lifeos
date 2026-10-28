"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { MailReader } from "@/components/MailReader";
import { Alert, Badge, Button, Card, Empty, PageHeader, Spinner, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { MAIL_KEY, senderLabel, subjectLabel } from "@/lib/mail";
import { spaceParam, useSpace } from "@/lib/space";
import type { MailSummary, Page } from "@/lib/types";

function MailView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { selected, spaceName } = useSpace();
  const selectedId = searchParams.get("id");

  const mails = useInfiniteQuery({
    queryKey: [MAIL_KEY, "list", selected],
    queryFn: ({ pageParam }) =>
      api<Page<MailSummary>>(
        `/v1/mail/messages${spaceParam(selected)}${pageParam ? `${selected ? "&" : "?"}cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = mails.data?.pages.flatMap((p) => p.items) ?? [];
  const open = (id: string) => router.push(`${pathname}?id=${encodeURIComponent(id)}`);
  const close = () => router.replace(pathname, { scroll: false });

  return (
    <>
      <PageHeader title="E-mails" />
      <div className="grid gap-4 md:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <Card
          title="Reçus"
          className={cx("max-h-[75vh] overflow-y-auto", selectedId && "hidden md:block")}
          footer="Copies locales des libellés suivis, lues toutes les 15 minutes environ et gardées 90 jours."
        >
          {mails.isPending ? (
            <Spinner />
          ) : mails.error ? (
            <Alert tone="error">{errorMessage(mails.error)}</Alert>
          ) : items.length === 0 ? (
            <Empty>
              Aucun e-mail. Choisissez les libellés à suivre dans{" "}
              <Link href="/settings" className="text-teal-700 underline dark:text-teal-400">
                Réglages &gt; Comptes connectés
              </Link>
              .
            </Empty>
          ) : (
            <ul aria-label="E-mails reçus" className="flex flex-col divide-y divide-stone-200 dark:divide-stone-800">
              {items.map((mail) => (
                <li key={mail.id}>
                  <button
                    type="button"
                    onClick={() => open(mail.id)}
                    aria-current={mail.id === selectedId ? "true" : undefined}
                    className={cx(
                      "flex w-full min-w-0 flex-col gap-0.5 rounded px-2 py-2 text-left text-sm hover:bg-stone-100 dark:hover:bg-stone-800",
                      mail.id === selectedId && "bg-teal-50 dark:bg-teal-950",
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">{senderLabel(mail)}</span>
                      <span className="shrink-0 text-xs text-stone-500">{formatDateTime(mail.received_at)}</span>
                    </span>
                    <span className="truncate">{subjectLabel(mail.subject)}</span>
                    <span className="truncate text-xs text-stone-500">{mail.snippet}</span>
                    <span className="flex flex-wrap gap-1">
                      {!selected && <Badge>{spaceName(mail.space_id)}</Badge>}
                      {mail.attachments > 0 && (
                        <Badge>
                          {mail.attachments} pièce{mail.attachments > 1 ? "s" : ""} jointe{mail.attachments > 1 ? "s" : ""}
                        </Badge>
                      )}
                      {mail.sensitivity.length > 0 && <Badge tone="violet">Sensible</Badge>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {mails.hasNextPage && (
            <div className="mt-2">
              <Button size="sm" variant="secondary" onClick={() => void mails.fetchNextPage()} disabled={mails.isFetchingNextPage}>
                Plus anciens
              </Button>
            </div>
          )}
        </Card>
        <Card className={cx(!selectedId && "hidden md:block")}>
          {selectedId ? (
            <MailReader key={selectedId} mailId={selectedId} onClose={close} />
          ) : (
            <Empty>Choisissez un e-mail à lire.</Empty>
          )}
        </Card>
      </div>
    </>
  );
}

export default function MailPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <MailView />
    </Suspense>
  );
}
