"use client";

import { useId, useState } from "react";

import { Badge, Button, Empty } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { LLMUsage } from "@/lib/types";
import {
  errorCodeLabel,
  formatCount,
  formatUsd,
  modelLabel,
  outcomeInfo,
  purposeLabel,
  redactionsLabel,
  tierLabel,
  tokensLabel,
} from "@/lib/usage";

type Call = LLMUsage["recent_calls"][number];

const COLLAPSED = 10;

function ErrorCode({ code }: { code: string | null }) {
  if (!code) return <span className="text-stone-400">—</span>;
  const explanation = errorCodeLabel(code);
  return (
    <span title={explanation}>
      <code className="text-xs">{code}</code>
      {explanation !== code && <span className="sr-only"> : {explanation}</span>}
    </span>
  );
}

/** Recent cloud calls (metadata only: LifeOS never stores what was sent). */
export function UsageCalls({ calls, paid }: { calls: LLMUsage["recent_calls"]; paid: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const captionId = useId();
  if (calls.length === 0) return <Empty>Aucun appel à l&apos;IA pour le moment.</Empty>;
  const visible: Call[] = expanded ? calls : calls.slice(0, COLLAPSED);

  return (
    <div className="flex flex-col gap-2">
      <p id={captionId} className="text-xs text-stone-500">
        Métadonnées seulement : le contenu envoyé n&apos;est jamais conservé. « Masqués » : données sensibles
        remplacées localement avant l&apos;envoi.
      </p>

      {/* Phone: one compact block per call. */}
      <ul className="divide-y divide-stone-100 text-sm sm:hidden dark:divide-stone-800" aria-describedby={captionId}>
        {visible.map((call) => {
          const outcome = outcomeInfo(call.outcome);
          return (
            <li key={call.id} className="flex flex-col gap-0.5 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate font-medium">{purposeLabel(call.purpose)}</span>
                <Badge tone={outcome.tone}>{outcome.label}</Badge>
              </div>
              <div className="text-xs text-stone-500">
                {formatDateTime(call.created_at)} · {tierLabel(call.tier)} · jetons {tokensLabel(call.input_tokens, call.output_tokens)}
              </div>
              <div className="flex flex-wrap gap-x-2 text-xs text-stone-500">
                <span className="break-all">{modelLabel(call.model)}</span>
                <span>· masqués : {redactionsLabel(call.redactions).toLowerCase()}</span>
                {paid && <span>· {formatUsd(call.cost_usd)}</span>}
                {call.error_code && (
                  <span>
                    · <ErrorCode code={call.error_code} />
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {/* Larger screens: a table. */}
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full text-left text-xs" aria-describedby={captionId}>
          <caption className="sr-only">Derniers appels à l&apos;IA</caption>
          <thead className="border-b border-stone-200 text-stone-500 dark:border-stone-800">
            <tr>
              <th scope="col" className="py-1.5 pr-3 font-medium">Date</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Usage</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Niveau</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Modèle</th>
              <th scope="col" className="py-1.5 pr-3 text-right font-medium">
                Jetons <span className="font-normal">(entrée / sortie)</span>
              </th>
              <th scope="col" className="py-1.5 pr-3 text-right font-medium">Masqués</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Résultat</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Code d&apos;erreur</th>
              {paid && (
                <th scope="col" className="py-1.5 text-right font-medium">Coût</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
            {visible.map((call) => {
              const outcome = outcomeInfo(call.outcome);
              return (
                <tr key={call.id} className="align-top">
                  <td className="whitespace-nowrap py-1.5 pr-3 text-stone-500">{formatDateTime(call.created_at)}</td>
                  <td className="py-1.5 pr-3">{purposeLabel(call.purpose)}</td>
                  <td className="py-1.5 pr-3">{tierLabel(call.tier)}</td>
                  <td className="max-w-40 break-all py-1.5 pr-3 text-stone-600 dark:text-stone-400">{modelLabel(call.model)}</td>
                  <td className="whitespace-nowrap py-1.5 pr-3 text-right tabular-nums">
                    {tokensLabel(call.input_tokens, call.output_tokens)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{formatCount(call.redactions)}</td>
                  <td className="py-1.5 pr-3">
                    <Badge tone={outcome.tone}>{outcome.label}</Badge>
                  </td>
                  <td className="py-1.5 pr-3">
                    <ErrorCode code={call.error_code} />
                  </td>
                  {paid && (
                    <td className="whitespace-nowrap py-1.5 text-right tabular-nums">{formatUsd(call.cost_usd)}</td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {calls.length > COLLAPSED && (
        <div>
          <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Afficher moins d'appels" : `Afficher les ${calls.length} derniers appels`}
          </Button>
        </div>
      )}
    </div>
  );
}
