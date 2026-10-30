"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { DocumentDetailPanel } from "@/components/DocumentDetail";
import { DocumentList } from "@/components/DocumentList";
import { DocumentUpload } from "@/components/DocumentUpload";
import { PageHeader, Spinner, cx } from "@/components/ui";

function DocumentsView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");

  const openDocument = (id: string) => router.push(`${pathname}?id=${encodeURIComponent(id)}`);
  const closeDocument = () => router.replace(pathname, { scroll: false });

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
          <DocumentUpload onUploaded={(document) => openDocument(document.id)} />
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
