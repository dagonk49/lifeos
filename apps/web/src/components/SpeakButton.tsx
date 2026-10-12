"use client";

import { useEffect, useRef, useState } from "react";

import { localFrenchVoices, speakableText } from "@/lib/voice";

function useLocalFrenchVoice(): SpeechSynthesisVoice | null {
  const [voice, setVoice] = useState<SpeechSynthesisVoice | null>(null);
  useEffect(() => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const synth = window.speechSynthesis;
    // Voices load asynchronously in some browsers.
    const update = () => setVoice(localFrenchVoices(synth.getVoices())[0] ?? null);
    update();
    synth.addEventListener("voiceschanged", update);
    return () => synth.removeEventListener("voiceschanged", update);
  }, []);
  return voice;
}

/**
 * Reads a text aloud with a French voice of the device (F01 « lecture vocale »). Only local
 * voices are used: a network voice would send the text to a third party. Hidden when the
 * device has none.
 */
export function SpeakButton({ text, className }: { text: string; className?: string }) {
  const voice = useLocalFrenchVoice();
  const [speaking, setSpeaking] = useState(false);
  const mine = useRef<SpeechSynthesisUtterance | null>(null);

  // Leaving the page stops this button's reading, not another one's.
  useEffect(
    () => () => {
      if (mine.current) window.speechSynthesis.cancel();
    },
    [],
  );

  if (!voice) return null;

  function toggle() {
    const synth = window.speechSynthesis;
    if (speaking) {
      // Some browsers fire no end event on cancel.
      mine.current = null;
      setSpeaking(false);
      synth.cancel();
      return;
    }
    synth.cancel(); // one reading at a time
    const utterance = new SpeechSynthesisUtterance(speakableText(text));
    utterance.voice = voice;
    utterance.lang = voice?.lang ?? "fr-FR";
    const done = () => {
      if (mine.current === utterance) mine.current = null;
      setSpeaking(false);
    };
    utterance.onend = done;
    utterance.onerror = done;
    mine.current = utterance;
    setSpeaking(true);
    synth.speak(utterance);
  }

  return (
    <button type="button" className={className ?? "underline"} onClick={toggle} aria-pressed={speaking}>
      {speaking ? "Arrêter la lecture" : "Écouter"}
    </button>
  );
}
