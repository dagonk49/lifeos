"use client";

import { useId } from "react";

import { Select } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { agentOptions, isAgentChoice, resourceList, type AgentChoice } from "@/lib/agents";
import type { AgentInfo } from "@/lib/types";

/**
 * Explicit choice of the agent that answers the next message. The general assistant is
 * the default; a specialised agent is never picked automatically.
 */
export function AgentSelector({
  agents,
  value,
  onChange,
  spaceName,
  error,
  disabled,
}: {
  agents: AgentInfo[] | undefined;
  value: AgentChoice;
  onChange: (agent: AgentChoice) => void;
  spaceName: string;
  error?: unknown;
  disabled?: boolean;
}) {
  const id = useId();
  const helpId = useId();
  const options = agentOptions(agents, value);
  const current = options.find((o) => o.value === value);

  return (
    <div className="flex min-w-0 flex-col gap-1 text-sm sm:max-w-sm sm:flex-1">
      <label htmlFor={id} className="font-medium text-stone-700 dark:text-stone-300">
        Agent
      </label>
      <Select
        id={id}
        value={value}
        disabled={disabled}
        aria-describedby={helpId}
        onChange={(e) => {
          if (isAgentChoice(e.target.value)) onChange(e.target.value);
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
      <div id={helpId} className="flex flex-col gap-0.5 text-xs text-stone-500">
        {current?.description && <p>{current.description}</p>}
        {current && current.missing.length > 0 && (
          <p className="text-amber-800 dark:text-amber-300">
            Accès en lecture à autoriser dans l&apos;espace « {spaceName} » : {resourceList(current.missing)}. Il
            vous sera demandé au premier envoi.
          </p>
        )}
        {error !== undefined && error !== null && <p>Agents spécialisés indisponibles : {errorMessage(error)}</p>}
      </div>
    </div>
  );
}
