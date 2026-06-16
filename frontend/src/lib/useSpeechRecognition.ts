import { useCallback, useEffect, useRef, useState } from "react";

// Minimal typings for the Web Speech API (not in lib.dom for all TS targets).
// We only declare what we use.
interface SpeechRecognitionAlternativeLike {
  transcript: string;
}
interface SpeechRecognitionResultLike {
  0: SpeechRecognitionAlternativeLike;
  isFinal: boolean;
  length: number;
}
interface SpeechRecognitionResultListLike {
  length: number;
  [index: number]: SpeechRecognitionResultLike;
}
interface SpeechRecognitionEventLike extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultListLike;
}
interface SpeechRecognitionErrorEventLike extends Event {
  error: string;
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: ((e: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function messageForError(code: string): string {
  switch (code) {
    case "network":
      // The API surface exists but the recognition service is unreachable.
      // This is the norm outside official Google Chrome (Electron, VS Code's
      // browser, Brave, etc. expose the interface but have no speech backend).
      return "speech recognition is unavailable in this browser — try Google Chrome";
    case "not-allowed":
    case "service-not-allowed":
      return "microphone permission was denied";
    case "audio-capture":
      return "no microphone was found";
    default:
      return code || "speech recognition error";
  }
}

interface Options {
  /** Called with the finalized chunk each time a result is marked final. */
  onFinalChunk: (text: string) => void;
  /** Called with the current interim (not-yet-final) transcript as it grows. */
  onInterim?: (text: string) => void;
  lang?: string;
}

interface Result {
  /** True only when the browser exposes the Web Speech API. */
  supported: boolean;
  listening: boolean;
  error: string | null;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

/**
 * Live speech-to-text via the browser's Web Speech API (Phase 1 — no backend).
 *
 * Interim results stream in as the user speaks; each finalized segment is
 * handed back via ``onFinalChunk`` so the caller can append it to the chat
 * draft. Entirely client-side and feature-detected: where the API is missing
 * (most non-Chromium browsers) ``supported`` is false and the mic UI hides.
 *
 * Privacy note: in Chrome, audio is sent to Google's servers for recognition.
 */
export function useSpeechRecognition({ onFinalChunk, onInterim, lang }: Options): Result {
  const [supported] = useState<boolean>(() => getCtor() !== null);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  // Keep the latest callbacks without re-creating the recognition instance.
  const finalRef = useRef(onFinalChunk);
  const interimRef = useRef(onInterim);
  finalRef.current = onFinalChunk;
  interimRef.current = onInterim;

  useEffect(() => {
    const Ctor = getCtor();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = lang ?? (typeof navigator !== "undefined" ? navigator.language : "en-US");

    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        const text = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalRef.current(text);
        } else {
          interim += text;
        }
      }
      interimRef.current?.(interim);
    };
    rec.onerror = (e) => {
      // "no-speech" / "aborted" are routine; surface only the actionable ones.
      if (e.error === "no-speech" || e.error === "aborted") return;
      setError(messageForError(e.error));
    };
    rec.onend = () => setListening(false);

    recognitionRef.current = rec;
    return () => {
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
      try {
        rec.abort();
      } catch {
        /* already stopped */
      }
      recognitionRef.current = null;
    };
  }, [lang]);

  const start = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec || listening) return;
    setError(null);
    try {
      rec.start();
      setListening(true);
    } catch {
      // start() throws if called while already started; ignore.
    }
  }, [listening]);

  const stop = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    try {
      rec.stop();
    } catch {
      /* already stopped */
    }
    setListening(false);
  }, []);

  const toggle = useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  return { supported, listening, error, start, stop, toggle };
}
