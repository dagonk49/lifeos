"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ConnectorCallback } from "@/components/ConnectorCallback";
import { PageHeader, Spinner } from "@/components/ui";

function Callback() {
  const search = useSearchParams();
  return <ConnectorCallback search={search} />;
}

/** Redirect URI of the connected accounts (`LIFEOS_PUBLIC_ORIGIN` + /connexions/retour). */
export default function ConnectorReturnPage() {
  return (
    <>
      <PageHeader title="Comptes connectés" />
      <Suspense fallback={<Spinner />}>
        <Callback />
      </Suspense>
    </>
  );
}
