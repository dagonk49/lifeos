// Pure helpers for the « achat important » evaluation (task 28, UC03): the finance and
// coaching agents compute locally, the assistant writes one recommendation. No React here.

import { resourceList } from "./agents";
import { formatMoney } from "./finance";
import type {
  Confidence,
  FindingAgent,
  PurchaseFigure,
  PurchaseGoal,
  PurchaseReason,
  PurchaseResult,
  PurchaseStep,
  StepAgent,
  Verdict,
} from "./types";

export const PURCHASE_PATH = "/v1/agents/workflows/purchase";
export const LABEL_MAX = 120;

export function runPath(runId: string): string {
  return `/v1/agents/runs/${encodeURIComponent(runId)}`;
}

export const AGENT_NAMES_FR: Record<StepAgent, string> = {
  finance: "Finances",
  coaching: "Coaching",
  core: "Assistant général",
};

export const VERDICT_LABELS: Record<Verdict, string> = {
  raisonnable: "Achat raisonnable",
  "à reporter": "Achat à reporter",
  déconseillé: "Achat déconseillé",
  incertain: "Avis incertain",
};

export const VERDICT_TONES: Record<Verdict, "teal" | "amber" | "red" | "neutral"> = {
  raisonnable: "teal",
  "à reporter": "amber",
  déconseillé: "red",
  incertain: "neutral",
};

export function confidenceLabel(confidence: Confidence): string {
  return `Confiance ${confidence}`;
}

/** "1,5" from "1.5": a decimal shown the French way, without going through a float. */
function frenchDecimal(value: string): string {
  return value.replace("-", "−").replace(".", ",");
}

/** A figure as computed by the agent: money, percentage or months. */
export function figureValue(figure: Pick<PurchaseFigure, "value" | "unit">): string {
  if (figure.unit === "EUR") return formatMoney(figure.value);
  if (figure.unit === "%") return `${frenchDecimal(figure.value)} %`;
  return `${frenchDecimal(figure.value)} mois`;
}

export function figureRange(figure: Pick<PurchaseFigure, "low" | "high" | "unit">): string | null {
  if (figure.low === null || figure.high === null || figure.unit !== "EUR") return null;
  if (figure.low === figure.high) return null;
  return `entre ${formatMoney(figure.low)} et ${formatMoney(figure.high)}`;
}

/** One line on a goal, from the coaching agent's findings only. */
export function goalSummary(goal: PurchaseGoal): string {
  const parts: string[] = [];
  if (goal.progress !== null) parts.push(`${goal.progress} % atteint`);
  else parts.push("avancement non mesuré");
  if (goal.financial && goal.remaining !== null) parts.push(`reste ${formatMoney(goal.remaining)}`);
  if (goal.purchase_months !== null) {
    parts.push(`l'achat représente ${frenchDecimal(goal.purchase_months)} mois d'épargne prévue`);
  }
  return parts.join(" · ");
}

/** "Selon l'agent Finances (F8, F10)", or why a reason is not backed by a figure. */
export function reasonSource(reason: PurchaseReason): string {
  if (!reason.supported) return "Non appuyé sur un chiffre ou un objectif calculé";
  const agents = reason.agents.map((a: FindingAgent) => AGENT_NAMES_FR[a]);
  const who = agents.length > 1 ? `les agents ${agents.join(" et ")}` : `l'agent ${agents[0] ?? ""}`;
  return `Selon ${who} (${reason.refs.join(", ")})`;
}

/** « Ce qui a été consulté » for one step. */
export function stepSummary(step: PurchaseStep, maxCalls?: number): string {
  if (step.agent === "core") {
    const calls = step.cloud_calls === 1 ? "1 appel à l'IA" : `${step.cloud_calls} appel(s) à l'IA`;
    return `${calls}${maxCalls !== undefined ? ` sur ${maxCalls} autorisé${maxCalls > 1 ? "s" : ""}` : ""}, sur les seuls résultats des agents`;
  }
  if (step.missing.length > 0) return `Pas d'accès en lecture aux ${resourceList(step.missing)} : rien n'a été lu`;
  return `A lu : ${resourceList(step.resources_read)} (${step.items} élément${step.items > 1 ? "s" : ""})`;
}

/** Agents still to be granted for a complete evaluation. */
export function missingAgents(result: PurchaseResult): StepAgent[] {
  return result.steps.filter((s) => s.missing.length > 0).map((s) => s.agent);
}
