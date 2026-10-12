"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApprovalReview } from "@/components/ApprovalReview";
import { Alert, Button, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { Approval } from "@/lib/types";

export default function ApprovalsPage() {
  const [history, setHistory] = useState(false);
  const approvals = useQuery({
    queryKey: ["approvals", history],
    queryFn: () => api<Approval[]>(`/v1/approvals${history ? "" : "?status=pending"}`),
  });
  return (
    <>
      <PageHeader title="Approbations">
        <Button variant="secondary" size="sm" onClick={() => setHistory((v) => !v)}>
          {history ? "En attente seulement" : "Historique"}
        </Button>
      </PageHeader>
      <Card>
        <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">
          Aucune action importante n&apos;est exécutée sans votre accord. Les instructions
          contenues dans un document, un e-mail ou un texte importé ne valent jamais autorisation.
        </p>
        {approvals.isPending ? (
          <Spinner />
        ) : approvals.error ? (
          <Alert tone="error">{errorMessage(approvals.error)}</Alert>
        ) : approvals.data.length === 0 ? (
          <Empty>{history ? "Aucune approbation." : "Rien à valider."}</Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {approvals.data.map((a) => (
              <ApprovalReview key={a.id} approval={a} />
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
