import { useId, type ReactNode } from "react";

import { Alert, Badge } from "@/components/ui";
import { formatDate, formatDateTime, KIND_LABELS } from "@/lib/format";
import {
  andMore,
  asNumber,
  asRecord,
  asStrings,
  coveredDays,
  extraKeys,
  formatDecimal,
  formatPercent,
  GOAL_KIND_LABELS,
  goalValueText,
  humanizeKey,
  isAutomatic,
  KNOWN_SECTIONS,
  otherSections,
  plural,
  resourceLabel,
  sectionLabel,
} from "@/lib/reviews";
import type { ReviewFacts } from "@/lib/types";

const TASK_KEYS = ["completed", "completed_titles", "created", "overdue_open", "overdue_titles", "due_next_7_days"];
const JOURNAL_KEYS = ["entries", "days_with_entry", "mood_average", "mood_entries"];
const GOAL_KEYS = [
  "title",
  "kind",
  "due_date",
  "metric_progress",
  "current_value",
  "target_value",
  "unit",
  "checkins_in_period",
  "milestones_done_in_period",
];
const MEMORY_KEYS = ["facts_added", "by_kind", "decisions", "events"];
const ASSISTANT_KEYS = ["messages_sent"];

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-md bg-stone-50 px-3 py-2 dark:bg-stone-800/60">
      <dt className="text-xs text-stone-600 dark:text-stone-400">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
      {hint && <dd className="text-xs text-muted">{hint}</dd>}
    </div>
  );
}

function Stats({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">{children}</dl>;
}

function TitleList({ title, items, total }: { title: string; items: string[]; total: number }) {
  if (items.length === 0) return null;
  const more = andMore(total, items.length);
  return (
    <div className="mt-3">
      <h5 className="text-sm font-medium">{title}</h5>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
        {items.map((item, i) => (
          <li key={i} className="break-words">
            {item}
          </li>
        ))}
      </ul>
      {more && <p className="mt-0.5 text-xs text-muted">… {more}</p>}
    </div>
  );
}

function FactSection({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="border-t border-stone-100 pt-3 first:border-t-0 first:pt-0 dark:border-stone-800">
      <h4 id={id} className="mb-2 text-sm font-semibold text-stone-800 dark:text-stone-200">
        {title}
      </h4>
      {children}
    </section>
  );
}

/** Renders any JSON value readably (sections or fields this page does not know yet). */
function GenericValue({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="text-muted">—</span>;
  if (typeof value === "boolean") return value ? "Oui" : "Non";
  if (typeof value === "number") return formatDecimal(value) || String(value);
  if (typeof value === "string") return <span className="break-words">{value}</span>;
  if (depth > 3) return <code className="break-all text-xs">{JSON.stringify(value)}</code>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted">Aucun élément</span>;
    return (
      <ul className="list-disc space-y-0.5 pl-5">
        {value.map((item, i) => (
          <li key={i}>
            <GenericValue value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }
  const record = asRecord(value);
  if (!record) return String(value);
  return <GenericFields value={record} keys={Object.keys(record)} depth={depth + 1} />;
}

function GenericFields({ value, keys, depth = 0 }: { value: Record<string, unknown>; keys: string[]; depth?: number }) {
  if (keys.length === 0) return null;
  return (
    <dl className="grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[minmax(8rem,auto)_1fr]">
      {keys.map((key) => (
        <div key={key} className="contents">
          <dt className="text-stone-600 dark:text-stone-400">{humanizeKey(key)}</dt>
          <dd className="min-w-0">
            <GenericValue value={value[key]} depth={depth} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Extras({ value, known }: { value: Record<string, unknown>; known: string[] }) {
  const keys = extraKeys(value, known);
  if (keys.length === 0) return null;
  return (
    <div className="mt-3">
      <GenericFields value={value} keys={keys} />
    </div>
  );
}

function TasksFacts({ value }: { value: Record<string, unknown> }) {
  const completed = asNumber(value.completed) ?? 0;
  const overdue = asNumber(value.overdue_open) ?? 0;
  return (
    <>
      <Stats>
        <Stat label="Terminées" value={completed} />
        <Stat label="Créées" value={asNumber(value.created) ?? 0} />
        <Stat label="En retard, encore ouvertes" value={overdue} />
        <Stat
          label="À échéance sous 7 jours"
          value={asNumber(value.due_next_7_days) ?? 0}
          hint="À partir du calcul"
        />
      </Stats>
      <TitleList title="Tâches terminées" items={asStrings(value.completed_titles)} total={completed} />
      <TitleList title="Tâches en retard" items={asStrings(value.overdue_titles)} total={overdue} />
      <Extras value={value} known={TASK_KEYS} />
    </>
  );
}

function JournalFacts({ value, period }: { value: Record<string, unknown>; period: ReviewFacts["period"] }) {
  const covered = coveredDays(period);
  const mood = asNumber(value.mood_average);
  const moodEntries = asNumber(value.mood_entries) ?? 0;
  return (
    <>
      <Stats>
        <Stat label="Entrées" value={asNumber(value.entries) ?? 0} />
        <Stat
          label="Jours avec une entrée"
          value={`${asNumber(value.days_with_entry) ?? 0} / ${covered.days}`}
          hint={covered.days < covered.total ? `${plural(covered.days, "jour écoulé", "jours écoulés")} sur ${covered.total}` : undefined}
        />
        <Stat
          label="Humeur moyenne"
          value={mood === null ? "—" : `${formatDecimal(mood, 1)} / 5`}
          hint={mood === null ? "Aucune humeur notée" : `Sur ${plural(moodEntries, "entrée notée", "entrées notées")}`}
        />
      </Stats>
      <Extras value={value} known={JOURNAL_KEYS} />
    </>
  );
}

function GoalFacts({ goal }: { goal: Record<string, unknown> }) {
  const title = typeof goal.title === "string" ? goal.title : "Objectif";
  const kind = typeof goal.kind === "string" ? goal.kind : null;
  const ratio = asNumber(goal.metric_progress);
  const values = goalValueText(goal.current_value, goal.target_value, goal.unit);
  const checkins = asNumber(goal.checkins_in_period) ?? 0;
  const milestones = asNumber(goal.milestones_done_in_period) ?? 0;
  const due = typeof goal.due_date === "string" ? goal.due_date : null;
  return (
    <li className="rounded-md border border-stone-200 p-2.5 dark:border-stone-800">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium break-words">{title}</span>
        {kind && <Badge>{GOAL_KIND_LABELS[kind] ?? kind}</Badge>}
      </div>
      {ratio !== null && (
        <div className="mt-2 flex items-center gap-2">
          <div
            role="progressbar"
            aria-label={`Progression de « ${title} »`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(Math.min(1, Math.max(0, ratio)) * 100)}
            className="h-2 flex-1 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700"
          >
            <div className="h-full rounded-full bg-teal-600" style={{ width: `${Math.min(1, Math.max(0, ratio)) * 100}%` }} />
          </div>
          <span className="text-xs tabular-nums text-stone-600 dark:text-stone-400">{formatPercent(ratio)}</span>
        </div>
      )}
      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-stone-600 dark:text-stone-400">
        {values && <span>{values}</span>}
        <span>{plural(checkins, "point d'étape", "points d'étape")} sur la période</span>
        <span>{plural(milestones, "jalon atteint", "jalons atteints")}</span>
        {due && <span>Échéance : {formatDate(due)}</span>}
      </p>
      <Extras value={goal} known={GOAL_KEYS} />
    </li>
  );
}

function GoalsFacts({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return <GenericValue value={value} />;
  if (value.length === 0) return <p className="text-sm text-muted">Aucun objectif actif dans cet espace.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {value.map((goal, i) => {
        const record = asRecord(goal);
        return record ? (
          <GoalFacts key={i} goal={record} />
        ) : (
          <li key={i}>
            <GenericValue value={goal} />
          </li>
        );
      })}
    </ul>
  );
}

function MemoryFacts({ value }: { value: Record<string, unknown> }) {
  const added = asNumber(value.facts_added) ?? 0;
  const byKind = asRecord(value.by_kind) ?? {};
  const kinds = Object.entries(byKind).filter((entry): entry is [string, number] => typeof entry[1] === "number");
  const decisions = asStrings(value.decisions);
  const events = asStrings(value.events);
  return (
    <>
      <p className="flex flex-wrap items-center gap-1.5 text-sm">
        <span>{plural(added, "fait ajouté", "faits ajoutés")} à la mémoire</span>
        {kinds.map(([kind, count]) => (
          <Badge key={kind}>
            {KIND_LABELS[kind] ?? humanizeKey(kind)} : {count}
          </Badge>
        ))}
      </p>
      <TitleList title="Décisions" items={decisions} total={asNumber(byKind.decision) ?? decisions.length} />
      <TitleList title="Événements" items={events} total={asNumber(byKind.event) ?? events.length} />
      <Extras value={value} known={MEMORY_KEYS} />
    </>
  );
}

function AssistantFacts({ value }: { value: Record<string, unknown> }) {
  return (
    <>
      <Stats>
        <Stat label="Messages envoyés à l'assistant" value={asNumber(value.messages_sent) ?? 0} />
      </Stats>
      <Extras value={value} known={ASSISTANT_KEYS} />
    </>
  );
}

function KnownSection({ name, value, period }: { name: string; value: unknown; period: ReviewFacts["period"] }) {
  if (name === "goals") return <GoalsFacts value={value} />;
  const record = asRecord(value);
  if (!record) return <GenericValue value={value} />;
  switch (name) {
    case "tasks":
      return <TasksFacts value={record} />;
    case "journal":
      return <JournalFacts value={record} period={period} />;
    case "memory":
      return <MemoryFacts value={record} />;
    case "assistant":
      return <AssistantFacts value={record} />;
    default:
      return <GenericValue value={record} />;
  }
}

/** Block a): deterministic facts computed locally, never written by the model. */
export function ReviewFactsBlock({ facts, generatedBy }: { facts: ReviewFacts; generatedBy: string }) {
  const headingId = useId();
  const period = facts.period;
  const omitted = asStrings(facts.omitted);
  const known = KNOWN_SECTIONS.filter((key) => facts[key] !== undefined);
  const others = otherSections(facts);
  const empty = known.length === 0 && others.length === 0;

  return (
    <section
      aria-labelledby={headingId}
      className="rounded-lg border border-stone-200 p-3 sm:p-4 dark:border-stone-700"
    >
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="text-base font-semibold">
          Faits (calculés localement, sans IA)
        </h3>
        {period?.computed_at && (
          <span className="text-xs text-muted">Calculés le {formatDateTime(period.computed_at)}</span>
        )}
      </header>

      {period?.partial && (
        <div className="mb-3">
          <Alert tone="info">
            <strong>Période en cours</strong> : ces faits s&apos;arrêtent au moment du calcul. Établissez le bilan
            à nouveau plus tard pour les compléter.
          </Alert>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {known.map((key) => (
          <FactSection key={key} title={sectionLabel(key)}>
            <KnownSection name={key} value={facts[key]} period={period} />
          </FactSection>
        ))}
        {others.map((key) => (
          <FactSection key={key} title={sectionLabel(key)}>
            <GenericValue value={facts[key]} />
          </FactSection>
        ))}
        {empty && <p className="text-sm text-muted">Aucune donnée n&apos;a pu être calculée pour cette période.</p>}
      </div>

      {omitted.length > 0 && (
        <div className="mt-3 rounded-md border border-dashed border-stone-300 p-2.5 text-sm dark:border-stone-700">
          <p className="flex flex-wrap items-center gap-1.5">
            <span className="font-medium">Non accessibles :</span>
            {omitted.map((key) => (
              <Badge key={key} tone="amber">
                {resourceLabel(key)}
              </Badge>
            ))}
          </p>
          <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
            {isAutomatic(generatedBy)
              ? "L'agent « coaching » qui a établi ce bilan n'avait pas la permission de lire ces données : elles ne figurent pas dans le bilan."
              : "Ces données n'étaient pas lisibles au moment du calcul : elles ne figurent pas dans le bilan."}
          </p>
        </div>
      )}
    </section>
  );
}
