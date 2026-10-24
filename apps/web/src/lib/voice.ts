// Pure helpers for voice: dictation (UC01, transcribed on the LifeOS server) and reading
// answers aloud (browser speech synthesis, local voices only). No React here.

import type { Dictation } from "./types";

/** Recording formats in order of preference: Opus in WebM (Chrome, Edge, Firefox), Opus in
 * Ogg (Firefox), AAC in MP4 (Safari). The server checks the actual type by signature. */
export const RECORDING_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"] as const;

/** The first supported recording type, "" to let the browser choose, null if none. */
export function pickRecordingType(isSupported: ((type: string) => boolean) | undefined): string | null {
  if (!isSupported) return null;
  return RECORDING_TYPES.find((type) => isSupported(type)) ?? "";
}

/** File name sent with the recording (informative only). */
export function recordingFileName(mimeType: string): string {
  if (mimeType.includes("ogg")) return "dictee.ogg";
  if (mimeType.includes("mp4")) return "dictee.m4a";
  return "dictee.webm";
}

/** "0:07", "1:05", "15:00". */
export function formatSeconds(total: number): string {
  const seconds = Math.max(0, Math.floor(total));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

const FAILURES: Record<string, string> = {
  too_long: "L'enregistrement dépasse la durée maximale.",
  unreadable: "L'enregistrement est illisible. Réessayez.",
  unsupported_type: "Ce format audio n'est pas pris en charge.",
  unsupported_codec: "Ce format audio n'est pas pris en charge.",
  too_large: "L'enregistrement est trop volumineux.",
  too_slow: "La lecture de l'enregistrement a pris trop de temps. Réessayez.",
  transcription_crashed: "La transcription a échoué plusieurs fois de suite sur cet enregistrement.",
  original_purged: "L'enregistrement a expiré avant d'être transcrit.",
  original_unreadable: "L'enregistrement n'a pas pu être relu sur le serveur.",
  processing_error: "La transcription a échoué. Réessayez.",
};

const WAITING: Record<string, string> = {
  transcription_unavailable: "Transcription momentanément indisponible : nouvel essai automatique…",
  documents_unavailable: "Stockage indisponible : nouvel essai automatique…",
};

/** What to tell the owner about a dictation in progress or finished. */
export function dictationStatusText(dictation: Pick<Dictation, "status" | "error">): string {
  if (dictation.status === "failed") return FAILURES[dictation.error ?? ""] ?? "La transcription a échoué.";
  if (dictation.status === "ready") return "Transcription terminée.";
  const waiting = dictation.error ? WAITING[dictation.error] : undefined;
  if (waiting) return waiting;
  return dictation.status === "processing" ? "Transcription en cours sur le serveur…" : "En attente de transcription…";
}

export function isDictationDone(dictation: Pick<Dictation, "status"> | undefined): boolean {
  return dictation?.status === "ready" || dictation?.status === "failed";
}

/** Appends a transcript to a draft, separated by a blank line (or a space in a one-liner). */
export function appendTranscript(draft: string, transcript: string, { multiline = true } = {}): string {
  const text = transcript.trim();
  if (!text) return draft;
  const base = draft.replace(/\s+$/, "");
  if (!base) return text;
  return `${base}${multiline ? "\n\n" : " "}${text}`;
}

/** Speech synthesis voices that read French on the device itself: network voices (e.g.
 * some Chrome voices) would send the text to a third party. */
export function localFrenchVoices<T extends Pick<SpeechSynthesisVoice, "lang" | "localService" | "default">>(
  voices: readonly T[],
): T[] {
  const french = voices.filter((v) => v.localService && v.lang.toLowerCase().replace("_", "-").startsWith("fr"));
  // Prefer France French, then the default voice.
  return [...french].sort((a, b) => score(b) - score(a));
}

function score(voice: Pick<SpeechSynthesisVoice, "lang" | "default">): number {
  return (voice.lang.toLowerCase().replace("_", "-") === "fr-fr" ? 2 : 0) + (voice.default ? 1 : 0);
}

/** Text worth reading aloud: markdown-ish markers removed, length bounded. */
export function speakableText(text: string, max = 4000): string {
  const cleaned = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[*_#>`]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.length > max ? `${cleaned.slice(0, max)}…` : cleaned;
}
