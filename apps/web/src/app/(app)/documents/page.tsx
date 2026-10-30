"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { DocumentDetailPanel } from "@/components/DocumentDetail";
import { DocumentList } from "@/components/DocumentList";
import { DocumentUpload } from "@/components/DocumentUpload";
import { Alert, PageHeader, Spinner, cx } from "@/components/ui";
import { receiveSharedFiles, SHARE_FAILED, SHARE_PARAM, SHARE_READY, sharedFileNotice } from "@/lib/share";
import type { DocumentDetail } from "@/lib/types";

/** Files shared to LifeOS from the phone (manifest share target, ADR 0022). */
type Shared =
  | { status: "none" }
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; files: File[]; index: number; last: DocumentDetail | null };

function initialShared(param: string | null): Shared {
  if (param === SHARE_READY) return { status: "loading" };
  if (param === SHARE_FAILED) return { status: "failed" };
  return { status: "none" };
}

/**
 * Asks the service worker once for the files shared from the phone, then drops the query
 * parameter (a reload must not ask again). The files stay in this page's memory only.
 */
function useSharedFiles(param: string | null, clearParam: () => void) {
  const [shared, setShared] = useState<Shared>(() => initialShared(param));
  const asked = useRef(false);
  useEffect(() => {
    if (param === null || asked.current) return;
    asked.current = true;
    if (param !== SHARE_READY) {
      clearParam();
      return;
    }
    void receiveSharedFiles().then((files) => {
      setShared(files && files.length > 0 ? { status: "ready", files, index: 0, last: null } : { status: "failed" });
      clearParam();
    });
  }, [param, clearParam]);
  return [shared, setShared] as const;
}

function DocumentsView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");

  const openDocument = (id: string) => router.push(`${pathname}?id=${encodeURIComponent(id)}`);
  const closeDocument = () => router.replace(pathname, { scroll: false });
  const clearShareParam = useCallback(() => router.replace(pathname, { scroll: false }), [router, pathname]);
  const [shared, setShared] = useSharedFiles(searchParams.get(SHARE_PARAM), clearShareParam);
  const sharedFile = shared.status === "ready" ? (shared.files[shared.index] ?? null) : null;

  const uploaded = (document: DocumentDetail) => {
    if (shared.status === "ready" && shared.index + 1 < shared.files.length) {
      // The next shared file, in a fresh form; the documents sent stay in the list.
      setShared({ ...shared, index: shared.index + 1, last: document });
      return;
    }
    if (shared.status !== "none") setShared({ status: "none" });
    openDocument(document.id);
  };

  return (
    <>
      <PageHeader title="Documents" />
      <p className="-mt-2 mb-4 max-w-3xl text-sm text-stone-600 dark:text-stone-400">
        Vos fichiers sont chiffrés sur le serveur et lus localement (OCR) : ils ne sont jamais envoyés dans le cloud.
        Les documents sensibles (identité, santé, banque) ne sont jamais transmis à l&apos;assistant IA.
      </p>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        {/* On a phone, the open document replaces the list; "Fermer" brings it back. */}
        <div className={cx("flex flex-col gap-4", selectedId && "hidden lg:flex")}>
          {shared.status === "loading" && <Spinner label="Réception du fichier partagé…" />}
          {shared.status === "failed" && (
            <Alert tone="warning">
              Le fichier partagé n&apos;a pas pu être repris. Choisissez-le ci-dessous avec « Fichier ».
            </Alert>
          )}
          {shared.status === "ready" && (
            <Alert tone="info">
              {shared.last && <>« {shared.last.title} » est envoyé : lecture en cours. </>}
              {sharedFileNotice(shared.index, shared.files.length)}
            </Alert>
          )}
          <DocumentUpload
            key={shared.status === "ready" ? `shared-${shared.index}` : "upload"}
            initialFile={sharedFile}
            onUploaded={uploaded}
          />
          <DocumentList selectedId={selectedId} />
        </div>

        {selectedId ? (
          <DocumentDetailPanel key={selectedId} documentId={selectedId} onClose={closeDocument} />
        ) : (
          <div className="hidden rounded-lg border border-dashed border-stone-300 p-6 text-center text-sm text-muted lg:block dark:border-stone-700">
            Sélectionnez un document pour voir son texte, le corriger ou télécharger l&apos;original.
          </div>
        )}
      </div>
    </>
  );
}

export default function DocumentsPage() {
  // useSearchParams needs a Suspense boundary for the static build.
  return (
    <Suspense fallback={<Spinner />}>
      <DocumentsView />
    </Suspense>
  );
}
