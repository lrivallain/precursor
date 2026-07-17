import { useCallback, useEffect, useRef, useState } from "react";
import type { ConversationTranscriber } from "microsoft-cognitiveservices-speech-sdk";
import { api } from "./api";

/** A finalized, diarized transcript phrase handed back to the caller. */
export interface FinalSegment {
  text: string;
  speakerLabel: string | null;
  offsetMs: number;
}

/** An audio input device offered in the capture picker. */
export interface AudioInputDevice {
  deviceId: string;
  label: string;
}

interface Options {
  /** Called for each finalized, diarized phrase. */
  onFinalSegment: (segment: FinalSegment) => void;
  /** Called with the current interim (in-progress) transcript. */
  onInterim?: (text: string) => void;
  /** Whether Azure STT is configured server-side (gates capture). */
  enabled: boolean;
  /** BCP-47 language tag, e.g. "en-US" / "fr-FR". */
  lang?: string;
  /** Selected input deviceId (e.g. a virtual loopback). Undefined = default. */
  deviceId?: string;
  /** Also capture the default microphone and mix it with the selected device. */
  captureMic?: boolean;
  /**
   * Called after a *transparent reconnect* (an Azure ~20-min 1006 drop) has
   * re-established the live session — not on the initial connect. Azure
   * re-numbers speakers on each new session, so the caller should start a fresh
   * voice-attribution namespace from here.
   */
  onReconnect?: () => void;
}

interface Result {
  supported: boolean;
  listening: boolean;
  error: string | null;
  start: () => void;
  stop: () => void;
}

/**
 * Enumerate audio input devices for the capture picker. Requests microphone
 * permission first so device *labels* are populated (browsers hide labels until
 * the user has granted access at least once). The temporary stream is released
 * immediately — we only needed it to unlock labels.
 */
export async function listAudioInputDevices(): Promise<AudioInputDevice[]> {
  try {
    const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
    for (const track of probe.getTracks()) track.stop();
  } catch {
    // Permission denied / no device — enumerate anyway (labels may be blank).
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((d) => d.kind === "audioinput")
    .map((d, i) => ({
      deviceId: d.deviceId,
      label: d.label || `Microphone ${i + 1}`,
    }));
}

/**
 * Live, diarized speech-to-text via **Azure ConversationTranscriber**.
 *
 * The browser captures a selected input device (typically a virtual loopback
 * carrying the meeting's audio) — optionally mixed with the local microphone —
 * and streams it to Azure directly using the Speech SDK. The subscription key
 * never reaches the client: the backend mints a short-lived token
 * (``GET /api/stt/token``). Each finalized phrase is returned with its speaker
 * label (``Guest-1`` …) and an offset from the recording start.
 *
 * Changing language mid-session requires a stop()/start() cycle (the caller
 * orchestrates it); Azure can't switch the recognizer's language in place.
 *
 * Azure closes a continuous-transcription WebSocket after ~20 minutes regardless
 * of token validity (surfaced as "StatusCode: 1006"). Rather than end the
 * recording, we transparently re-establish the session — re-minting a token and
 * re-opening the capture device(s) — so a long meeting survives repeated
 * 20-minute cycles. `listening` stays true throughout so the timeline (and
 * LiveView's per-run diarization namespace) is uninterrupted; `offsetMs` is
 * measured from the original recording start, so timestamps stay continuous
 * across the brief reconnect gap. The attempt counter resets after any
 * successful (re)connect and only guards against a genuinely unreachable service
 * (real outage) turning into an infinite reconnect loop.
 */
const MAX_RECONNECT_ATTEMPTS = 6;

export function useConversationTranscriber({
  onFinalSegment,
  onInterim,
  enabled,
  lang,
  deviceId,
  captureMic,
  onReconnect,
}: Options): Result {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const transcriberRef = useRef<ConversationTranscriber | null>(null);
  // Synchronous latch: transcriberRef is only assigned after several awaits
  // (token fetch, SDK import, getUserMedia), so a rapid double-click could pass
  // the transcriberRef guard twice and spin up two recordings. This flag is set
  // before those awaits so the second call is rejected immediately.
  const startingRef = useRef(false);
  // We own the captured MediaStreams (rather than letting the SDK open the
  // device) so teardown can stop their tracks and reliably release the OS
  // devices — the SDK's close() alone leaves the capture indicator on.
  const streamsRef = useRef<MediaStream[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  // Wall-clock start of the recording. Persisted across reconnects so segment
  // offsets stay continuous; reset to 0 by start() and stamped on first connect.
  const startMsRef = useRef<number>(0);

  // Azure auth tokens live ~10 min; a long recording would otherwise be canceled
  // mid-stream when the token expires. We periodically mint a fresh token and
  // hand it to the live transcriber to keep the session alive.
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

  // Latest callbacks/options mirrored into refs so the SDK event handlers and
  // the reconnect path (both registered once) always see current values.
  const finalRef = useRef(onFinalSegment);
  const interimRef = useRef(onInterim);
  const langRef = useRef(lang);
  const deviceIdRef = useRef(deviceId);
  const captureMicRef = useRef(captureMic);
  const onReconnectRef = useRef(onReconnect);
  finalRef.current = onFinalSegment;
  interimRef.current = onInterim;
  langRef.current = lang;
  deviceIdRef.current = deviceId;
  captureMicRef.current = captureMic;
  onReconnectRef.current = onReconnect;
  // Latest closures, so transcriber event handlers (captured at connect time)
  // always reach the current reconnect logic without stale references.
  const startSessionRef = useRef<(() => Promise<void>) | undefined>(undefined);
  const scheduleReconnectRef = useRef<(() => void) | undefined>(undefined);

  const clearTimers = useCallback((): void => {
    if (refreshTimerRef.current !== null) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const releaseMedia = useCallback((): void => {
    for (const stream of streamsRef.current) {
      for (const track of stream.getTracks()) {
        try {
          track.stop();
        } catch {
          /* already stopped */
        }
      }
    }
    streamsRef.current = [];
    const ctx = audioCtxRef.current;
    audioCtxRef.current = null;
    if (ctx && ctx.state !== "closed") void ctx.close().catch(() => {});
  }, []);

  const teardown = useCallback((): void => {
    userStoppedRef.current = true;
    reconnectingRef.current = false;
    clearTimers();
    const t = transcriberRef.current;
    transcriberRef.current = null;
    if (t) {
      try {
        t.stopTranscribingAsync(
          () => {
            t.close();
            releaseMedia();
          },
          () => {
            t.close();
            releaseMedia();
          },
        );
      } catch {
        releaseMedia();
      }
    } else {
      releaseMedia();
    }
    setListening(false);
  }, [clearTimers, releaseMedia]);

  useEffect(() => teardown, [teardown]);

  // Establish (or re-establish) a live transcription session. Acquires a fresh
  // token and capture device(s) each time so a reconnect never reuses a socket
  // or track the SDK may already have torn down. Throws on failure so the caller
  // (initial start() or the reconnect loop) can react.
  const startSession = useCallback(async (): Promise<void> => {
    // True when this connect is re-establishing a dropped session (set by
    // scheduleReconnect) rather than the initial start; drives the onReconnect
    // notification below once the new session is live.
    const isReconnect = reconnectingRef.current;
    clearTimers();
    const { token, endpoint, language } = await api.stt.getToken();
    // Lazy-loaded so the ~450KB SDK only ships when capture is used.
    const sdk = await import("microsoft-cognitiveservices-speech-sdk");
    const speechConfig = sdk.SpeechConfig.fromEndpoint(new URL(endpoint));
    speechConfig.authorizationToken = token;
    speechConfig.speechRecognitionLanguage =
      langRef.current ||
      language ||
      (typeof navigator !== "undefined" ? navigator.language : "en-US");

    const deviceId = deviceIdRef.current;
    // Open the selected input device (the loopback carrying meeting audio).
    const primary = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    });
    const streams: MediaStream[] = [primary];

    // Optionally mix in the default microphone so both sides of a hybrid
    // meeting (in-room + remote) are transcribed.
    let feed = primary;
    if (captureMicRef.current && deviceId) {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      streams.push(mic);
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      const dest = ctx.createMediaStreamDestination();
      ctx.createMediaStreamSource(primary).connect(dest);
      ctx.createMediaStreamSource(mic).connect(dest);
      feed = dest.stream;
    }
    streamsRef.current = streams;

    const audioConfig = sdk.AudioConfig.fromStreamInput(feed);
    const transcriber = new sdk.ConversationTranscriber(speechConfig, audioConfig);

    transcriber.transcribing = (_s, e) => {
      interimRef.current?.(e.result.text);
    };
    transcriber.transcribed = (_s, e) => {
      if (e.result.reason === sdk.ResultReason.RecognizedSpeech && e.result.text.trim()) {
        finalRef.current({
          text: e.result.text,
          speakerLabel: e.result.speakerId || null,
          offsetMs: Math.max(0, Date.now() - startMsRef.current),
        });
        interimRef.current?.("");
      }
    };
    transcriber.canceled = (_s, e) => {
      if (userStoppedRef.current) return;
      if (e.reason === sdk.CancellationReason.Error) {
        lastErrorRef.current = e.errorDetails || "Speech connection lost";
      }
      scheduleReconnectRef.current?.();
    };
    transcriber.sessionStopped = () => {
      if (userStoppedRef.current) return;
      scheduleReconnectRef.current?.();
    };

    transcriberRef.current = transcriber;
    await new Promise<void>((resolve, reject) => {
      transcriber.startTranscribingAsync(
        () => resolve(),
        (err) => reject(new Error(typeof err === "string" ? err : "Could not start capture")),
      );
    });

    // Connected. Stamp the recording start on the first connect only (reconnects
    // keep the original so offsets stay continuous), clear any transient
    // error/backoff state, and re-arm the token refresh so this session survives
    // its own ~10 min token lifetime.
    if (startMsRef.current === 0) startMsRef.current = Date.now();
    reconnectingRef.current = false;
    reconnectAttemptsRef.current = 0;
    lastErrorRef.current = null;
    setError(null);
    setListening(true);
    // Azure re-numbers speakers on each new session, so let the caller open a
    // fresh voice-attribution namespace (and mark the boundary) after a reconnect.
    if (isReconnect) onReconnectRef.current?.();
    refreshTimerRef.current = setInterval(
      () => {
        void (async () => {
          const t = transcriberRef.current;
          if (!t) return;
          try {
            const refreshed = await api.stt.getToken();
            t.authorizationToken = refreshed.token;
          } catch {
            /* Transient failure; the next tick retries before expiry. */
          }
        })();
      },
      8 * 60 * 1000,
    );
  }, [clearTimers]);
  startSessionRef.current = startSession;

  const scheduleReconnect = useCallback((): void => {
    if (userStoppedRef.current || reconnectingRef.current) return;
    reconnectingRef.current = true;
    // Drop the dead transcriber and its capture stream(s) before re-acquiring;
    // the next startSession() opens a fresh token, socket, and MediaStream(s).
    const t = transcriberRef.current;
    transcriberRef.current = null;
    if (t) {
      try {
        t.close();
      } catch {
        /* already closed */
      }
    }
    clearTimers();
    releaseMedia();

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
        // Couldn't even re-establish; free the guard and retry (or give up once
        // the attempt budget is spent).
        reconnectingRef.current = false;
        scheduleReconnectRef.current?.();
      });
    }, backoff);
  }, [clearTimers, releaseMedia]);
  scheduleReconnectRef.current = scheduleReconnect;

  const start = useCallback(() => {
    if (!enabled || transcriberRef.current || startingRef.current || reconnectingRef.current) return;
    startingRef.current = true;
    setError(null);
    userStoppedRef.current = false;
    reconnectingRef.current = false;
    reconnectAttemptsRef.current = 0;
    lastErrorRef.current = null;
    startMsRef.current = 0;
    void startSession()
      .catch((e) => {
        setError(e instanceof Error ? e.message : "Could not reach the speech service");
        teardown();
      })
      .finally(() => {
        // Latch released once transcriberRef is set (guarding further starts) or
        // teardown cleared it after a failure, freeing a retry.
        startingRef.current = false;
      });
  }, [enabled, startSession, teardown]);

  const stop = useCallback(() => teardown(), [teardown]);

  return { supported: enabled, listening, error, start, stop };
}
