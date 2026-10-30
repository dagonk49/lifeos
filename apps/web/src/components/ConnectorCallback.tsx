"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Alert, Card, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { ACCOUNTS_KEY, callbackParams, capabilityList, type CallbackParams } from "@/lib/connectors";
import { useSpace } from "@/lib/space";
import type { OAuthResult } from "@/lib/types";

/** Removes the code and the state from the address bar and the history entry. */
function stripQuery() {
  window.history.replaceState(null, "", window.location.pathname);
}

function BackLink() {
  return (
    <Link
      href="/settings#connexions"
      className="mt-3 inline-block rounded-md bg-teal-700 px-3.5 py-2 text-sm font-medium text-white hover:bg-teal-800"
    >
      Revenir aux comptes connectés
    </Link>
  );
}

/**
 * Return from the provider's consent page (ADR 0012). The session cookie is SameSite=Strict:
 * it is not sent on the provider's cross-site redirect, so this page (same-site) hands the
 * code and the state to the API, once, after removing them from the URL.
 */
export function ConnectorCallback({ search }: { search: URLSearchParams }) {
  const queryClient = useQueryClient();
  const { spaceName } = useSpace();
  // Read once: the URL is cleaned right away and the code is single-use.
  const [params] = useState<CallbackParams | null>(() => callbackParams(search));
  const sent = useRef(false);
  const complete = useMutation({
    mutationFn: (body: CallbackParams) =>
      api<OAuthResult>("/v1/connectors/oauth/callback", { method: "POST", body }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ACCOUNTS_KEY }),
  });
  const { mutate } = complete;

  useEffect(() => {
    stripQuery();
    if (sent.current || !params) return;
    sent.current = true;
    mutate(params);
  }, [params, mutate]);

  if (!params) {
    return (
      <Card title="Connexion d'un compte">
        <Alert tone="error">
          Ce retour de connexion est incomplet : aucun compte n&apos;a été connecté. Recommencez depuis les réglages.
        </Alert>
        <BackLink />
      </Card>
    );
  }
  if (complete.error) {
    return (
      <Card title="Connexion d'un compte">
        <Alert tone="error">{errorMessage(complete.error)}</Alert>
        <BackLink />
      </Card>
    );
  }
  if (!complete.data) {
    return <Spinner label="Connexion du compte en cours…" />;
  }

  const result = complete.data;
  const space = spaceName(result.space_id) || "choisi";
  if (result.reconnected) {
    return (
      <Card title="Compte reconnecté">
        <Alert tone="success">{result.account.email ?? "Le compte"} est reconnecté.</Alert>
        <p className="mt-2 text-sm">Ses autorisations par espace sont inchangées.</p>
        {result.missing.length > 0 && (
          <div className="mt-2">
            <Alert tone="warning">
              Non accordé chez le fournisseur cette fois : {capabilityList(result.missing)}. Ces usages restent coupés
              dans tous les espaces jusqu&apos;à ce que vous les acceptiez de nouveau chez le fournisseur (décochez puis
              recochez-les dans les réglages).
            </Alert>
          </div>
        )}
        <BackLink />
      </Card>
    );
  }
  return (
    <Card title="Compte connecté">
      <Alert tone="success">{result.account.email ?? "Le compte"} est connecté.</Alert>
      {result.granted.length > 0 && (
        <p className="mt-2 text-sm">
          Autorisé dans l&apos;espace « {space} » : {capabilityList(result.granted)}.
        </p>
      )}
      {result.missing.length > 0 && (
        <div className="mt-2">
          <Alert tone="warning">
            Non accordé chez le fournisseur : {capabilityList(result.missing)}. Ces usages restent coupés ; pour les
            ajouter, cochez-les dans les réglages et acceptez-les chez le fournisseur.
          </Alert>
        </div>
      )}
      <BackLink />
    </Card>
  );
}
