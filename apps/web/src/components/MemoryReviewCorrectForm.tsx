"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { MEMORY_REVIEW_KEY } from "@/lib/memoryReview";
import type { Fact } from "@/lib/types";

/** Refreshes everything a change of fact affects: lists, « À vérifier » queues, dashboard. */
export function useInvalidateFacts(): () => Promise<void> {
  const queryClient = useQueryClient();
  return async () => {
    await Promise.all(
      ["facts", MEMORY_REVIEW_KEY, "dashboard"].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    );
  };
}

/**
 * Inline correction of a fact (POST /v1/memory/facts/{id}/correct): the old version is
 * kept as superseded. Shared by the facts list and the « À vérifier » tab.
 */
export function FactCorrectionForm({
  fact,
  onCancel,
  onCorrected,
}: {
  fact: Fact;
  onCancel: () => void;
  onCorrected: (corrected: Fact) => void;
}) {
  const invalidate = useInvalidateFacts();
  const [statement, setStatement] = useState(fact.statement);
  const correct = useMutation({
    mutationFn: () =>
      api<Fact>(`/v1/memory/facts/${fact.id}/correct`, { method: "POST", body: { statement: statement.trim() } }),
    onSuccess: async (corrected) => {
      await invalidate();
      onCorrected(corrected);
    },
  });

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (statement.trim()) correct.mutate();
      }}
    >
      <Input
        aria-label="Énoncé corrigé"
        value={statement}
        onChange={(e) => setStatement(e.target.value)}
        maxLength={2000}
        required
        autoFocus
      />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" type="submit" disabled={!statement.trim() || correct.isPending}>
          Enregistrer la correction
        </Button>
        <Button size="sm" variant="secondary" onClick={onCancel} disabled={correct.isPending}>
          Annuler
        </Button>
      </div>
      {correct.error && <Alert tone="error">{errorMessage(correct.error)}</Alert>}
    </form>
  );
}
