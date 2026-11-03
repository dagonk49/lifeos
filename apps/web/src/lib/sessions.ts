import type { SessionInfo } from "./types";

/** Sessions open on other devices (or other browsers) than this one. */
export function otherSessions(sessions: SessionInfo[] | undefined): SessionInfo[] {
  return (sessions ?? []).filter((session) => !session.current);
}

/** What « Déconnecter les autres sessions » did, in words. */
export function revokedOthersMessage(count: number): string {
  if (count === 0) return "Aucune autre session n'était ouverte.";
  if (count === 1) return "1 autre session a été déconnectée.";
  return `${count} autres sessions ont été déconnectées.`;
}
