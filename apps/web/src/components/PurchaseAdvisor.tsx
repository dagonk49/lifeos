"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { Alert, Badge, Button, Card, Field, Input, Spinner } from "@/components/ui";
import { api, errorMessage, problemMessage } from "@/lib/api";
import { formatMoney, parseAmount } from "@/lib/finance";
import {
  AGENT_NAMES_FR,
  confidenceLabel,
  figureRange,
  figureValue,
  goalSummary,
  LABEL_MAX,
  missingAgents,
  PURCHASE_PATH,
  reasonSource,
  runPath,
  stepSummary,
  VERDICT_LABELS,
  VERDICT_TONES,
} from "@/lib/purchase";
import type { PurchaseResult } from "@/lib/types";

function Result({ result, onErased }: { result: PurchaseResult; onErased: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const erase = useMutation({
    mutationFn: () => api<void>(runPath(result.run_id), { method: "DELETE" }),
    onSuccess: onErased,
  });
  const advice = result.advice;
  const missing = missingAgents(result);
  const finance = result.figures;
  return (
    <div className="mt-4 flex flex-col gap-4 text-sm" aria-live="polite">
      <h3 className="text-base font-semibold">
        {result.question.label} : {formatMoney(result.question.amount)}
      </h3>

      {result.error && (
        <Alert tone={result.error.code === "agent_permission_required" ? "warning" : "error"}>
          {problemMessage(result.error.code, result.error.title)}{" "}
          {result.figures.length > 0 && "Les chiffres calculés localement restent ci-dessous."}
        </Alert>
      )}
      {missing.length > 0 && (
        <p className="text-stone-700 dark:text-stone-300">
          Sans accès, {missing.map((a) => `l'agent ${AGENT_NAMES_FR[a]}`).join(" et ")} n&apos;a rien lu.{" "}
          <Link href="/settings#agents" className="font-medium text-teal-800 underline dark:text-teal-300">
            Gérer l&apos;accès des agents
          </Link>
        </p>
      )}

      {advice && (
        <section aria-labelledby={`advice-${result.run_id}`}>
          <div className="flex flex-wrap items-center gap-2">
            <h4 id={`advice-${result.run_id}`} className="font-semibold">
              Recommandation
            </h4>
            <Badge tone={VERDICT_TONES[advice.verdict]}>{VERDICT_LABELS[advice.verdict]}</Badge>
            <Badge>{confidenceLabel(advice.confidence)}</Badge>
          </div>
          {advice.recommendation ? (
            <p className="mt-1 whitespace-pre-line">{advice.recommendation}</p>
          ) : (
            <p className="mt-1 text-muted">L&apos;assistant n&apos;a pas formulé de recommandation.</p>
          )}
          {advice.reasons.length > 0 && (
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5">
              {advice.reasons.map((reason, index) => (
                <li key={index}>
                  {reason.text}{" "}
                  <span className={reason.supported ? "text-muted" : "text-amber-800 dark:text-amber-200"}>
                    — {reasonSource(reason)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {(advice.hypotheses.length > 0 || advice.uncertainties.length > 0) && (
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-stone-700 dark:text-stone-300">
              {advice.hypotheses.map((text, index) => (
                <li key={`h${index}`}>Hypothèse : {text}</li>
              ))}
              {advice.uncertainties.map((text, index) => (
                <li key={`u${index}`}>Incertitude : {text}</li>
              ))}
            </ul>
          )}
          {advice.unmatched_amounts.length > 0 && (
            <div className="mt-2">
              <Alert tone="warning">
                Montants écrits par l&apos;assistant qui ne correspondent à aucun calcul :{" "}
                {advice.unmatched_amounts.join(", ")}. Les chiffres de référence sont ceux ci-dessous.
              </Alert>
            </div>
          )}
          {advice.unknown_refs.length > 0 && (
            <p className="mt-1 text-xs text-muted">
              Références inconnues écartées : {advice.unknown_refs.join(", ")}.
            </p>
          )}
        </section>
      )}

      {finance.length > 0 && (
        <section aria-labelledby={`figures-${result.run_id}`}>
          <h4 id={`figures-${result.run_id}`} className="font-semibold">
            Chiffres de l&apos;agent Finances (calculés sur votre serveur)
          </h4>
          <ul className="mt-1 flex flex-col gap-1">
            {finance.map((figure) => {
              const range = figureRange(figure);
              return (
                <li key={figure.key} className="flex flex-col">
                  <span>
                    <span className="text-muted">{figure.key}</span> {figure.label} :{" "}
                    <strong className="tabular-nums">{figureValue(figure)}</strong>
                    {range && <span className="text-muted"> ({range})</span>}
                  </span>
                  <span className="text-xs text-muted">{figure.basis}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {result.goals.length > 0 && (
        <section aria-labelledby={`goals-${result.run_id}`}>
          <h4 id={`goals-${result.run_id}`} className="font-semibold">
            Objectifs relevés par l&apos;agent Coaching
          </h4>
          <ul className="mt-1 flex flex-col gap-1">
            {result.goals.map((goal) => (
              <li key={goal.key}>
                <span className="text-muted">{goal.key}</span> {goal.title}
                {goal.financial && (
                  <>
                    {" "}
                    <Badge tone="blue">financier</Badge>
                  </>
                )}{" "}
                <span className="text-muted">— {goalSummary(goal)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.hypotheses.length > 0 && (
        <section aria-labelledby={`hyp-${result.run_id}`}>
          <h4 id={`hyp-${result.run_id}`} className="font-semibold">
            Hypothèses des calculs
          </h4>
          <ul className="mt-1 flex list-disc flex-col gap-1 pl-5 text-stone-700 dark:text-stone-300">
            {result.hypotheses.map((h, index) => (
              <li key={index}>
                {AGENT_NAMES_FR[h.agent]} : {h.text}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby={`steps-${result.run_id}`}>
        <h4 id={`steps-${result.run_id}`} className="font-semibold">
          Ce qui a été consulté
        </h4>
        <ul className="mt-1 flex flex-col gap-1">
          {result.steps.map((step) => (
            <li key={step.agent}>
              <strong>{AGENT_NAMES_FR[step.agent]}</strong> ({step.role.toLowerCase()}) :{" "}
              {stepSummary(step, step.agent === "core" ? result.max_cloud_calls : undefined)}
            </li>
          ))}
        </ul>
        <p className="mt-1 text-xs text-muted">
          Aucun agent n&apos;achète, ne déplace d&apos;argent ni ne modifie un objectif : la décision vous appartient.
          Évaluation conservée 90 jours.
        </p>
      </section>

      <div className="flex flex-wrap items-center gap-2">
        {confirming ? (
          <>
            <Button variant="danger" size="sm" disabled={erase.isPending} onClick={() => erase.mutate()}>
              Effacer l&apos;évaluation « {result.question.label} »
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Garder l&apos;évaluation
            </Button>
          </>
        ) : (
          <Button variant="secondary" size="sm" onClick={() => setConfirming(true)}>
            Effacer cette évaluation
          </Button>
        )}
        {erase.isError && <Alert tone="error">{errorMessage(erase.error)}</Alert>}
      </div>
    </div>
  );
}

/** UC03: « Puis-je faire cet achat ? », answered by the finance and coaching agents. */
export function PurchaseAdvisor({ spaceId }: { spaceId: string }) {
  const amountHint = useId();
  const [amount, setAmount] = useState("");
  const [label, setLabel] = useState("");
  const [amountError, setAmountError] = useState<string | null>(null);
  const [labelError, setLabelError] = useState<string | null>(null);
  const [result, setResult] = useState<PurchaseResult | null>(null);
  const evaluate = useMutation({
    mutationFn: (body: { space_id: string; amount: string; label: string }) =>
      api<PurchaseResult>(PURCHASE_PATH, { method: "POST", body, idempotent: true }),
    onSuccess: setResult,
  });

  const checkAmount = () => {
    const parsed = parseAmount(amount);
    setAmountError(parsed.ok ? null : parsed.error);
    return parsed.ok ? parsed.value : null;
  };
  const checkLabel = () => {
    const text = label.trim();
    setLabelError(text ? null : "Indiquez ce que vous envisagez d'acheter.");
    return text || null;
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = checkAmount();
    const text = checkLabel();
    if (value === null || text === null) return;
    setResult(null);
    evaluate.mutate({ space_id: spaceId, amount: value, label: text });
  };

  return (
    <Card title="Évaluer un achat important">
      <p className="mb-3 text-sm text-stone-700 dark:text-stone-300">
        L&apos;agent Finances calcule votre marge, l&apos;agent Coaching la compare à vos objectifs, chacun avec ses
        seuls accès ; puis l&apos;assistant formule une recommandation en citant leurs chiffres (un seul appel à
        l&apos;IA, sur ces résultats seulement).
      </p>
      <form noValidate onSubmit={submit} className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] sm:items-end">
        <Field label="Ce que vous envisagez d'acheter">
          <Input
            value={label}
            maxLength={LABEL_MAX}
            aria-invalid={labelError ? true : undefined}
            onChange={(e) => {
              setLabel(e.target.value);
              if (labelError) setLabelError(null);
            }}
            onBlur={() => label && checkLabel()}
          />
        </Field>
        <Field label="Montant (€)">
          <Input
            value={amount}
            inputMode="decimal"
            autoComplete="off"
            aria-describedby={amountError ? amountHint : undefined}
            aria-invalid={amountError ? true : undefined}
            onChange={(e) => {
              setAmount(e.target.value);
              if (amountError) setAmountError(null);
            }}
            onBlur={() => amount && checkAmount()}
          />
        </Field>
        <Button type="submit" disabled={evaluate.isPending}>
          Évaluer l&apos;achat
        </Button>
      </form>
      {(labelError || amountError) && (
        <p id={amountHint} className="mt-2 text-sm text-red-800 dark:text-red-300">
          {[labelError, amountError].filter(Boolean).join(" ")}
        </p>
      )}
      {evaluate.isPending && (
        <div className="mt-3">
          <Spinner label="Les agents calculent…" />
        </div>
      )}
      {evaluate.isError && (
        <div className="mt-3">
          <Alert tone="error">{errorMessage(evaluate.error)}</Alert>
        </div>
      )}
      {result && (
        <Result
          result={result}
          onErased={() => {
            setResult(null);
            evaluate.reset();
          }}
        />
      )}
    </Card>
  );
}
