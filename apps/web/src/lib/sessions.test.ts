import { describe, expect, it } from "vitest";

import { otherSessions, revokedOthersMessage } from "./sessions";
import type { SessionInfo } from "./types";

function session(id: string, current: boolean): SessionInfo {
  return {
    id,
    created_at: "2026-10-20T08:00:00Z",
    authenticated_at: "2026-10-20T08:00:00Z",
    last_seen_at: "2026-10-20T09:00:00Z",
    expires_at: "2026-11-19T08:00:00Z",
    user_agent: null,
    ip: null,
    current,
  };
}

describe("sessions", () => {
  it("keeps only the sessions of other devices", () => {
    expect(otherSessions([session("a", true), session("b", false)]).map((s) => s.id)).toEqual(["b"]);
    expect(otherSessions(undefined)).toEqual([]);
  });

  it("says how many sessions were signed out", () => {
    expect(revokedOthersMessage(0)).toBe("Aucune autre session n'était ouverte.");
    expect(revokedOthersMessage(1)).toBe("1 autre session a été déconnectée.");
    expect(revokedOthersMessage(3)).toBe("3 autres sessions ont été déconnectées.");
  });
});
