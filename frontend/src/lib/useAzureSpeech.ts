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
// Azure Speech closes a continuous-recognition WebSocket after ~20 minutes
// regardless of token validity (surfaced as "StatusCode: 1006"). Rather than
// end dictation, we transparently re-establish the session. The counter is
// reset after any successful (re)connect, so an hours-long recording survives
// repeated 20-minute cycles; it only guards against a genuinely unreachable
// service (real network outage) turning into an infinite reconnect loop.
const MAX_RECONNECT_ATTEMPTS = 6;

export function useAzureSpeech({ onFinalChunk, onInterim, enabled, lang }: Options): Result {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  // We own the microphone MediaStream (rather than letting the SDK open its
  // own) so teardown can stop its tracks and reliably release the OS mic — the
  // SDK's close() alone leaves the microphone indicator on in the browser.
  const streamRef = useRef<MediaStream | null>(null);
  // Azure auth tokens live ~10 min; a long recording would otherwise be
  // canceled mid-stream when the token expires. We periodically mint a fresh
  // token and hand it to the live recognizer to keep the session alive.
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set by an explicit stop() so an in-flight cancel/sessionStopped event
  // doesn't trigger a reconnect after the user asked to stop.
  const userStoppedRef = useRef(false);
  // Guards against overlapping reconnects (a drop fires both `canceled` and
  // `sessionStopped`) and paces retries against a persistently down service.
  const reconnectingRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const lastErrorRef = useRef<string | null>(null);
  // Latest closures, so recognizer event handlers (captured at connect time)
  // always reach the current reconnect logic without stale references.
  const startSessionRef = useRef<(() => Promise<void>) | undefined>(undefined);
  const scheduleReconnectRef = useRef<(() => void) | undefined>(undefined);
  const finalRef = useRef(onFinalChunk);
  const interimRef = useRef(onInterim);
  const langRef = useRef(lang);
  finalRef.current = onFinalChunk;
  interimRef.current = onInterim;
  langRef.current = lang;

  const clearTimers = useCallback(() => {
    if (refreshTimerRef.current !== null) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const stopStream = useCallback(() => {
    const stream = streamRef.current;
    streamRef.current = null;
    if (stream) {
      for (const track of stream.getTracks()) {
        try {
          track.stop();
        } catch {
          /* already stopped */
        }
      }
    }
  }, []);

  const releaseStream = useCallback(() => {
    clearTimers();
    stopStream();
  }, [clearTimers, stopStream]);

  const teardown = useCallback(() => {
    userStoppedRef.current = true;
    reconnectingRef.current = false;
    const rec = recognizerRef.current;
    recognizerRef.current = null;
    if (rec) {
      try {
        rec.stopContinuousRecognitionAsync(
          () => {
            rec.close();
            releaseStream();
          },
          () => {
            rec.close();
            releaseStream();
          },
        );
      } catch {
        releaseStream();
      }
    } else {
      releaseStream();
    }
    setListening(false);
  }, [releaseStream]);

  useEffect(() => teardown, [teardown]);

  // Establish (or re-establish) a live recognition session. Acquires a fresh
  // token and microphone stream each time so a reconnect never reuses a socket
  // or track the SDK may already have torn down.
  const startSession = useCallback(async () => {
    clearTimers();
    const { token, endpoint, language } = await api.stt.getToken();
    // Lazy-loaded so the ~450KB SDK only ships when dictation is actually
    // used (and only for users with Azure STT configured).
    const sdk = await import("microsoft-cognitiveservices-speech-sdk");
    const speechConfig = sdk.SpeechConfig.fromEndpoint(new URL(endpoint));
    speechConfig.authorizationToken = token;
    speechConfig.speechRecognitionLanguage =
      language ||
      langRef.current ||
      (typeof navigator !== "undefined" ? navigator.language : "en-US");
    // Own the mic stream so teardown can stop its tracks (releases the OS
    // mic; the SDK's close() alone doesn't reliably do so in the browser).
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamRef.current = stream;
    const audioConfig = sdk.AudioConfig.fromStreamInput(stream);
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
      if (userStoppedRef.current) return;
      if (e.reason === sdk.CancellationReason.Error) {
        lastErrorRef.current = e.errorDetails || "Speech connection lost";
      }
      scheduleReconnectRef.current?.();
    };
    recognizer.sessionStopped = () => {
      if (userStoppedRef.current) return;
      scheduleReconnectRef.current?.();
    };

    recognizerRef.current = recognizer;
    await new Promise<void>((resolve, reject) => {
      recognizer.startContinuousRecognitionAsync(
        () => resolve(),
        (err) => reject(new Error(typeof err === "string" ? err : "Could not start dictation")),
      );
    });

    // Connected. Clear any transient error/backoff state and re-arm the token
    // refresh so this session survives its own ~10 min token lifetime.
    reconnectingRef.current = false;
    reconnectAttemptsRef.current = 0;
    lastErrorRef.current = null;
    setError(null);
    setListening(true);
    refreshTimerRef.current = setInterval(
      () => {
        void (async () => {
          const rec = recognizerRef.current;
          if (!rec) return;
          try {
            const refreshed = await api.stt.getToken();
            rec.authorizationToken = refreshed.token;
          } catch {
            /* Transient failure; the next tick retries before expiry. */
          }
        })();
      },
      8 * 60 * 1000,
    );
  }, [clearTimers]);
  startSessionRef.current = startSession;

  const scheduleReconnect = useCallback(() => {
    if (userStoppedRef.current || reconnectingRef.current) return;
    reconnectingRef.current = true;
    // Drop the dead recognizer and its mic stream before re-acquiring; the
    // next startSession() opens a fresh token, socket, and MediaStream.
    const rec = recognizerRef.current;
    recognizerRef.current = null;
    if (rec) {
      try {
        rec.close();
      } catch {
        /* already closed */
      }
    }
    clearTimers();
    stopStream();

    const attempt = (reconnectAttemptsRef.current += 1);
    if (attempt > MAX_RECONNECT_ATTEMPTS) {
      reconnectingRef.current = false;
      setError(lastErrorRef.current || "Lost connection to the speech service");
      setListening(false);
      return;
    }

    const backoff = Math.min(500 * attempt, 4000);
    reconnectTimerRef.current = setTimeout(() => {
      void startSessionRef.current?.().catch(() => {
        // Couldn't even re-establish; free the guard and retry (or give up
        // once the attempt budget is spent).
        reconnectingRef.current = false;
        scheduleReconnectRef.current?.();
      });
    }, backoff);
  }, [clearTimers, stopStream]);
  scheduleReconnectRef.current = scheduleReconnect;

  const start = useCallback(() => {
    if (!enabled || recognizerRef.current) return;
    setError(null);
    userStoppedRef.current = false;
    reconnectingRef.current = false;
    reconnectAttemptsRef.current = 0;
    lastErrorRef.current = null;
    void startSession().catch((e) => {
      setError(e instanceof Error ? e.message : "Could not reach the speech service");
      teardown();
    });
  }, [enabled, startSession, teardown]);

  const stop = useCallback(() => teardown(), [teardown]);

  const toggle = useCallback(() => {
    if (listening || recognizerRef.current) stop();
    else start();
  }, [listening, start, stop]);

  return { supported: enabled, listening, error, start, stop, toggle };
}
