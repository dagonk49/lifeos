// Pure helpers of the home page (task 23): card registry and layout, agenda lines,
// sources and freshness. Instants come with the offset of the owner's zone, so the local
// time is read from the string itself (as in lib/calendar.ts), whatever the browser's zone.

import { formatDate, formatDateTime } from "./format";
import type { AgendaItem, CardId, CardSource, DashboardLayout, LayoutCard } from "./types";

/** Card ids in their default order, with their titles. */
export const CARDS: readonly { id: CardId; title: string }[] = [
  { id: "priorities", title: "Priorités du jour" },
  { id: "agenda", title: "Agenda du jour" },
  { id: "week", title: "Cette semaine" },
  { id: "suggestions", title: "Suggestions" },
  { id: "journal", title: "Journal" },
  { id: "approvals", title: "À valider" },
  { id: "memory", title: "Mémoire" },
  { id: "budget", title: "Budget" },
  { id: "assistant", title: "Assistant IA" },
];

export const CARD_IDS: readonly CardId[] = CARDS.map((c) => c.id);

export function cardTitle(id: CardId): string {
  return CARDS.find((c) => c.id === id)?.title ?? id;
}

/** Stored order first (unknown or repeated ids dropped), then every missing card, shown,
 * in the default order: mirrors dashboard.service.merge_cards. */
export function mergeLayout(stored: readonly { id: string; visible?: boolean }[] | undefined): LayoutCard[] {
  const seen = new Set<string>();
  const cards: LayoutCard[] = [];
  for (const entry of stored ?? []) {
    if ((CARD_IDS as readonly string[]).includes(entry.id) && !seen.has(entry.id)) {
      seen.add(entry.id);
      cards.push({ id: entry.id as CardId, visible: entry.visible ?? true });
    }
  }
  for (const id of CARD_IDS) if (!seen.has(id)) cards.push({ id, visible: true });
  return cards;
}

export const DEFAULT_LAYOUT: DashboardLayout = {
  cards: mergeLayout([]),
  default_view: "day",
  week_start: "monday",
  updated_at: null,
};

/** Moves the card at `index` by `delta` (-1: up, +1: down); unchanged at the edges. */
export function moveCard(cards: readonly LayoutCard[], index: number, delta: number): LayoutCard[] {
  const target = index + delta;
  if (index < 0 || index >= cards.length || target < 0 || target >= cards.length) return [...cards];
  const next = [...cards];
  const [card] = next.splice(index, 1);
  if (card) next.splice(target, 0, card);
  return next;
}

export function toggleCard(cards: readonly LayoutCard[], id: CardId, visible: boolean): LayoutCard[] {
  return cards.map((c) => (c.id === id ? { ...c, visible } : c));
}

/** Local "HH:MM" of an instant written with its offset. */
function clock(instant: string): string {
  return instant.slice(11, 16);
}

/** "Toute la journée", "09:30 – 10:00", "Échéance 18:00", "à partir de 22:00"… */
export function itemTime(item: AgendaItem): string {
  if (item.kind === "task") return item.start ? `Échéance ${clock(item.start)}` : "Échéance";
  if (item.all_day || !item.start) return "Toute la journée";
  const startDay = item.start.slice(0, 10);
  const endDay = item.end ? item.end.slice(0, 10) : startDay;
  const end = item.end ? clock(item.end) : clock(item.start);
  if (startDay === item.day && endDay === item.day) {
    return clock(item.start) === end ? clock(item.start) : `${clock(item.start)} – ${end}`;
  }
  if (startDay === item.day) return `à partir de ${clock(item.start)}`;
  if (endDay === item.day && end !== "00:00") return `jusqu'à ${end}`;
  return "Toute la journée";
}

const KIND_LABELS: Record<AgendaItem["kind"], string> = {
  event: "Événement",
  busy: "Occupé",
  task: "Tâche",
  milestone: "Jalon",
  payment: "Paiement prévu",
};

export function kindLabel(item: AgendaItem): string {
  return KIND_LABELS[item.kind];
}

/** "Source : Agenda perso · lu le 28/10/2026 10:30", "Objectif : Courir 10 km"… */
export function itemSource(item: AgendaItem): string | null {
  if (item.kind === "busy") return "Un autre de vos espaces (détails masqués)";
  if (item.kind === "milestone") return item.source_label ? `Objectif : ${item.source_label}` : null;
  if (item.kind === "event") {
    const read = item.synced_at ? ` · lu le ${formatDateTime(item.synced_at)}` : "";
    return `Source : ${item.source_label ?? "agenda"}${read}`;
  }
  return item.source_label ? `Source : ${item.source_label}` : null;
}

/** Counts of a day for the compact week card: "2 événements · 1 tâche". */
export function daySummary(items: readonly AgendaItem[]): string {
  const counts = new Map<string, number>();
  for (const item of items) {
    const key = item.kind === "busy" ? "event" : item.kind;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const words: [string, string, string][] = [
    ["event", "événement", "événements"],
    ["task", "tâche", "tâches"],
    ["milestone", "jalon", "jalons"],
    ["payment", "paiement prévu", "paiements prévus"],
  ];
  const parts = words
    .filter(([key]) => counts.has(key))
    .map(([key, one, many]) => {
      const n = counts.get(key) ?? 0;
      return `${n} ${n > 1 ? many : one}`;
    });
  return parts.length ? parts.join(" · ") : "Rien de prévu";
}

/** Footer of a card: what its figures rest on, and when. */
export function sourceFooter(source: CardSource | undefined, fallback: string): string {
  if (!source) return fallback;
  if (source.origin === "calendar") {
    const n = source.count ?? 0;
    const read = source.as_of ? `lus le ${formatDateTime(source.as_of)} au plus tôt` : "pas encore lus";
    return `${n} agenda${n > 1 ? "s" : ""} suivi${n > 1 ? "s" : ""}, ${read} · heures dans votre fuseau`;
  }
  if (source.origin === "review") {
    return `D'après vos bilans enregistrés (le plus récent : ${formatDate(source.as_of)}) · aucun appel à l'IA`;
  }
  return source.as_of ? `Calculé localement le ${formatDateTime(source.as_of)}` : "Calculé localement";
}

/** Where an item that came from somewhere leads (tasks, search hits, suggestions). */
export function sourceHref(sourceType: string | null | undefined, sourceId?: string | null): string | null {
  const id = sourceId ? encodeURIComponent(sourceId) : null;
  switch (sourceType) {
    case "goal":
      return id ? `/goals?id=${id}` : "/goals";
    case "document":
      return id ? `/documents?id=${id}` : "/documents";
    case "review":
      return id ? `/reviews?review=${id}` : "/reviews";
    case "mail_message":
      return id ? `/mail?id=${id}` : "/mail";
    case "journal":
    case "journal_entry":
      return "/journal";
    case "message":
    case "conversation":
      return "/chat";
    case "task":
      return "/tasks";
    case "fact":
    case "memory":
      return "/memory";
    case "calendar_event":
      return "/agenda";
    case "finance_recurring":
    case "transaction":
      return "/finances";
    default:
      return null;
  }
}

/** Label of a task's origin, when it did not come from the tasks page. */
export function taskSourceLabel(sourceType: string): string | null {
  if (sourceType === "journal_entry") return "Journal";
  if (sourceType === "message" || sourceType === "conversation") return "Assistant";
  return null;
}
