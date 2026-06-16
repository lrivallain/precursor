import { useCallback, useEffect, useRef, useState } from "react";
import type { SpeechRecognizer } from "microsoft-cognitiveservices-speech-sdk";
import { api } from "./api";

interface Options {
  /** Called with the finalized chunk each time a phrase is recognized. */
  onFinalChunk: (text: string) => void;
  /** Called with the current interim (in-progress) transcript. */
  onInterim?: (text: string) => void;
  /** Whether Azure STT is configured server-side (gates the mic UI). */
  enabled: boolean;
  /** BCP-47 language tag, e.g. "en-US" / "fr-FR". */
  lang?: string;
}

interface Result {
  /** True when Azure STT is configured and usable. */
  supported: boolean;
  listening: boolean;
  error: string | null;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

/**
 * Live speech-to-text via **Azure AI Speech** (Phase 2 — portable).
 *
 * The browser captures the mic and talks to Azure directly using the official
 * Speech SDK; the subscription key never reaches the client — the backend mints
 * a short-lived authorization token (``GET /api/stt/token``). Interim results
 * stream as the user speaks; each finalized phrase is handed back via
 * ``onFinalChunk``. Unlike the browser Web Speech API this works in any modern
 * browser, at the cost of an Azure resource.
 */
export function useAzureSpeech({ onFinalChunk, onInterim, enabled, lang }: Options): Result {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  const finalRef = useRef(onFinalChunk);
  const interimRef = useRef(onInterim);
  finalRef.current = onFinalChunk;
  interimRef.current = onInterim;

  const teardown = useCallback(() => {
    const rec = recognizerRef.current;
    recognizerRef.current = null;
    if (rec) {
      try {
        rec.stopContinuousRecognitionAsync(
          () => rec.close(),
          () => rec.close(),
        );
      } catch {
        /* already closing */
      }
    }
    setListening(false);
  }, []);

  useEffect(() => teardown, [teardown]);

  const start = useCallback(() => {
    if (!enabled || recognizerRef.current) return;
    setError(null);
    void (async () => {
      try {
        const { token, endpoint, language } = await api.getSttToken();
        // Lazy-loaded so the ~450KB SDK only ships when dictation is actually
        // used (and only for users with Azure STT configured).
        const sdk = await import("microsoft-cognitiveservices-speech-sdk");
        const speechConfig = sdk.SpeechConfig.fromEndpoint(new URL(endpoint));
        speechConfig.authorizationToken = token;
        speechConfig.speechRecognitionLanguage =
          language ||
          lang ||
          (typeof navigator !== "undefined" ? navigator.language : "en-US");
        const audioConfig = sdk.AudioConfig.fromDefaultMicrophoneInput();
        const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig);

        recognizer.recognizing = (_s, e) => {
          interimRef.current?.(e.result.text);
        };
        recognizer.recognized = (_s, e) => {
          if (e.result.reason === sdk.ResultReason.RecognizedSpeech && e.result.text.trim()) {
            finalRef.current(e.result.text);
            interimRef.current?.("");
          }
        };
        recognizer.canceled = (_s, e) => {
          if (e.errorDetails) setError(e.errorDetails);
          teardown();
        };
        recognizer.sessionStopped = () => teardown();

        recognizerRef.current = recognizer;
        recognizer.startContinuousRecognitionAsync(
          () => setListening(true),
          (err) => {
            setError(typeof err === "string" ? err : "Could not start dictation");
            teardown();
          },
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not reach the speech service");
        teardown();
      }
    })();
  }, [enabled, lang, teardown]);

  const stop = useCallback(() => teardown(), [teardown]);

  const toggle = useCallback(() => {
    if (listening || recognizerRef.current) stop();
    else start();
  }, [listening, start, stop]);

  return { supported: enabled, listening, error, start, stop, toggle };
}
