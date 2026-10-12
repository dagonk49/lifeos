import { describe, expect, it } from "vitest";

import {
  appendTranscript,
  dictationStatusText,
  formatSeconds,
  isDictationDone,
  localFrenchVoices,
  pickRecordingType,
  recordingFileName,
  speakableText,
} from "./voice";

describe("pickRecordingType", () => {
  it("prefers Opus in WebM, then Ogg, then MP4 (Safari)", () => {
    expect(pickRecordingType(() => true)).toBe("audio/webm;codecs=opus");
    expect(pickRecordingType((t) => t.startsWith("audio/ogg"))).toBe("audio/ogg;codecs=opus");
    expect(pickRecordingType((t) => t === "audio/mp4")).toBe("audio/mp4");
  });

  it("lets the browser choose when none is known, and reports no recorder", () => {
    expect(pickRecordingType(() => false)).toBe("");
    expect(pickRecordingType(undefined)).toBeNull();
  });

  it("names the file after the container", () => {
    expect(recordingFileName("audio/webm;codecs=opus")).toBe("dictee.webm");
    expect(recordingFileName("audio/ogg;codecs=opus")).toBe("dictee.ogg");
    expect(recordingFileName("audio/mp4")).toBe("dictee.m4a");
    expect(recordingFileName("")).toBe("dictee.webm");
  });
});

describe("formatSeconds", () => {
  it("formats minutes and seconds", () => {
    expect(formatSeconds(0)).toBe("0:00");
    expect(formatSeconds(7.9)).toBe("0:07");
    expect(formatSeconds(65)).toBe("1:05");
    expect(formatSeconds(900)).toBe("15:00");
    expect(formatSeconds(-3)).toBe("0:00");
  });
});

describe("dictation status", () => {
  it("explains failures, retries and progress in French", () => {
    expect(dictationStatusText({ status: "failed", error: "too_long" })).toBe(
      "L'enregistrement dépasse la durée maximale.",
    );
    expect(dictationStatusText({ status: "failed", error: "weird" })).toBe("La transcription a échoué.");
    expect(dictationStatusText({ status: "pending", error: "transcription_unavailable" })).toMatch(
      /nouvel essai automatique/,
    );
    expect(dictationStatusText({ status: "processing", error: null })).toBe(
      "Transcription en cours sur le serveur…",
    );
    expect(dictationStatusText({ status: "pending", error: null })).toBe("En attente de transcription…");
  });

  it("knows when polling can stop", () => {
    expect(isDictationDone({ status: "ready" })).toBe(true);
    expect(isDictationDone({ status: "failed" })).toBe(true);
    expect(isDictationDone({ status: "processing" })).toBe(false);
    expect(isDictationDone(undefined)).toBe(false);
  });
});

describe("appendTranscript", () => {
  it("adds the transcript after the draft", () => {
    expect(appendTranscript("", "  Bonjour. ")).toBe("Bonjour.");
    expect(appendTranscript("Matin : sport.\n", "Après-midi : courses.")).toBe(
      "Matin : sport.\n\nAprès-midi : courses.",
    );
    expect(appendTranscript("Rappelle-moi", "demain", { multiline: false })).toBe("Rappelle-moi demain");
    expect(appendTranscript("Inchangé", "   ")).toBe("Inchangé");
  });
});

describe("localFrenchVoices", () => {
  const voice = (lang: string, localService: boolean, isDefault = false) => ({
    lang,
    localService,
    default: isDefault,
  });

  it("keeps only French voices of the device, France first", () => {
    const voices = [
      voice("fr-FR", false), // network voice: would send the text to a third party
      voice("en-US", true, true),
      voice("fr-CA", true),
      voice("fr_FR", true),
    ];
    const result = localFrenchVoices(voices);
    expect(result.map((v) => v.lang)).toEqual(["fr_FR", "fr-CA"]);
    expect(localFrenchVoices([voice("fr-FR", false)])).toEqual([]);
  });
});

describe("speakableText", () => {
  it("drops markdown markers and code, and bounds the length", () => {
    expect(speakableText("**Important** : `code`\n\n```\nx = 1\n```\n# Fin")).toBe("Important : code Fin");
    expect(speakableText("a".repeat(10), 4)).toBe("aaaa…");
  });
});
