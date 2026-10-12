"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Alert, Button } from "@/components/ui";
import { api, errorMessage, upload } from "@/lib/api";
import type { Dictation, VoiceStatus } from "@/lib/types";
import {
  dictationStatusText,
  formatSeconds,
  isDictationDone,
  pickRecordingType,
  recordingFileName,
} from "@/lib/voice";

type Phase = "idle" | "starting" | "recording" | "sending" | "transcribing";
// The recorder stops this long before the server's limit: a timer fires late (always a
// little, much more in a background tab), and a recording over the limit is refused.
const STOP_MARGIN_S = 2;

function micError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      return "Accès au micro refusé. Autorisez-le dans le navigateur pour dicter.";
    }
    if (error.name === "NotFoundError") return "Aucun micro détecté.";
    if (error.name === "NotReadableError") return "Le micro est utilisé par une autre application.";
  }
  return errorMessage(error);
}

export function useVoiceStatus() {
  return useQuery({
    queryKey: ["voice-status"],
    queryFn: () => api<VoiceStatus>("/v1/voice/status"),
    staleTime: 5 * 60_000,
  });
}

/**
 * Records a dictation in the browser and has it transcribed on the LifeOS server (UC01).
 * The transcript is handed to `onTranscript` for the owner to read and correct: nothing is
 * saved by this component. The recording itself is deleted on the server once transcribed.
 */
export function VoiceRecorder({
  spaceId,
  onTranscript,
  label = "Dicter",
}: {
  spaceId: string | null;
  onTranscript: (text: string) => void;
  label?: string;
}) {
  const status = useVoiceStatus();
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dictationId, setDictationId] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const discard = useRef(false);
  const delivered = useRef<string | null>(null);
  const mounted = useRef(true);
  // Incremented by every start and by unmounting: a microphone granted for an older start
  // (double click, page left during the permission prompt) is released at once.
  const startToken = useRef(0);
  const startedAt = useRef(0);
  const stopTimer = useRef<number | null>(null);
  const maxSeconds = status.data?.max_seconds ?? 900;

  const supported =
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia);

  const send = useMutation({
    mutationFn: (blob: Blob) => {
      const form = new FormData();
      form.append("space_id", spaceId ?? "");
      form.append("file", blob, recordingFileName(blob.type));
      return upload<Dictation>("/v1/voice/dictations", form);
    },
    onSuccess: (dictation) => {
      setDictationId(dictation.id);
      setPhase("transcribing");
    },
    onError: (failure) => {
      setError(errorMessage(failure));
      setPhase("idle");
    },
  });

  const dictation = useQuery({
    queryKey: ["dictation", dictationId],
    queryFn: () => api<Dictation>(`/v1/voice/dictations/${dictationId}`),
    enabled: dictationId !== null,
    refetchInterval: (query) => (isDictationDone(query.state.data) ? false : 1500),
  });

  // Hands the transcript over once, then forgets the dictation on the server.
  useEffect(() => {
    const data = dictation.data;
    if (!data || !isDictationDone(data) || delivered.current === data.id) return;
    delivered.current = data.id;
    if (data.status === "ready") {
      const text = (data.text ?? "").trim();
      if (text) {
        onTranscript(text);
        setNotice("Texte ajouté : relisez-le et corrigez-le avant d'enregistrer.");
      } else {
        setNotice("Aucune parole n'a été reconnue.");
      }
    } else {
      setError(dictationStatusText(data));
    }
    setPhase("idle");
    setDictationId(null);
    void api(`/v1/voice/dictations/${data.id}`, { method: "DELETE" }).catch(() => undefined);
  }, [dictation.data, onTranscript]);

  // Display only: the automatic stop is a timer armed when the recording starts.
  useEffect(() => {
    if (phase !== "recording") return;
    const timer = window.setInterval(
      () => setSeconds((performance.now() - startedAt.current) / 1000),
      250,
    );
    return () => window.clearInterval(timer);
  }, [phase]);

  // Leaving the page while recording (or while the microphone is being granted) releases
  // the microphone and sends nothing.
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      startToken.current += 1;
      discard.current = true;
      if (stopTimer.current !== null) window.clearTimeout(stopTimer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  if (!supported || status.isPending || !status.data?.transcription) return null;

  async function start() {
    if (phase !== "idle" || recorder.current) return;
    setError(null);
    setNotice(null);
    if (!spaceId) {
      setError("Choisissez d'abord un espace.");
      return;
    }
    const token = ++startToken.current;
    setPhase("starting");
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      if (!mounted.current || token !== startToken.current) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      const type = pickRecordingType((t) => MediaRecorder.isTypeSupported(t));
      const next = new MediaRecorder(media, type ? { mimeType: type } : undefined);
      // This recording's own chunks: nothing from another recorder can mix in.
      const chunks: Blob[] = [];
      discard.current = false;
      next.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      next.onstop = () => {
        if (stopTimer.current !== null) window.clearTimeout(stopTimer.current);
        stopTimer.current = null;
        media.getTracks().forEach((track) => track.stop());
        stream.current = null;
        recorder.current = null;
        if (discard.current || !mounted.current) {
          setPhase("idle");
          return;
        }
        const blob = new Blob(chunks, { type: next.mimeType || type || "audio/webm" });
        if (blob.size === 0) {
          setError("Rien n'a été enregistré.");
          setPhase("idle");
          return;
        }
        setPhase("sending");
        send.mutate(blob);
      };
      stream.current = media;
      recorder.current = next;
      startedAt.current = performance.now();
      next.start(1000);
      stopTimer.current = window.setTimeout(
        () => {
          if (next.state === "recording") next.stop();
        },
        Math.max(1, maxSeconds - STOP_MARGIN_S) * 1000,
      );
      setSeconds(0);
      setPhase("recording");
    } catch (failure) {
      if (!mounted.current || token !== startToken.current) return;
      setError(micError(failure));
      setPhase("idle");
    }
  }

  function stop(cancel = false) {
    discard.current = cancel;
    recorder.current?.stop();
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {phase === "recording" ? (
          <>
            <Button size="sm" variant="danger" onClick={() => stop()}>
              Terminer la dictée
            </Button>
            <Button size="sm" variant="ghost" onClick={() => stop(true)}>
              Annuler
            </Button>
            <span role="status" className="text-sm tabular-nums text-red-700 dark:text-red-400">
              <span aria-hidden="true">● </span>Enregistrement {formatSeconds(seconds)} / {formatSeconds(Math.max(1, maxSeconds - STOP_MARGIN_S))}
            </span>
          </>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => void start()} disabled={phase !== "idle"}>
            {phase === "starting" ? "Accès au micro…" : label}
          </Button>
        )}
        {phase === "sending" && (
          <span role="status" className="text-sm text-stone-600 dark:text-stone-400">
            Envoi de l'enregistrement…
          </span>
        )}
        {phase === "transcribing" && (
          <span role="status" className="text-sm text-stone-600 dark:text-stone-400">
            {dictation.data ? dictationStatusText(dictation.data) : "Transcription en cours sur le serveur…"}
          </span>
        )}
      </div>
      {phase === "idle" && !error && !notice && (
        <p className="text-xs text-stone-500">
          Transcrit sur votre serveur LifeOS, jamais dans le cloud ; l'enregistrement est supprimé
          ensuite.
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm text-teal-800 dark:text-teal-300">
          {notice}
        </p>
      )}
      {error && <Alert tone="error">{error}</Alert>}
    </div>
  );
}
