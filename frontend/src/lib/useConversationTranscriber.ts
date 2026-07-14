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
 */
export function useConversationTranscriber({
  onFinalSegment,
  onInterim,
  enabled,
  lang,
  deviceId,
  captureMic,
}: Options): Result {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const transcriberRef = useRef<ConversationTranscriber | null>(null);
  // We own the captured MediaStreams (rather than letting the SDK open the
  // device) so teardown can stop their tracks and reliably release the OS
  // devices — the SDK's close() alone leaves the capture indicator on.
  const streamsRef = useRef<MediaStream[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const startMsRef = useRef<number>(0);

  // Latest callbacks/options mirrored into refs so the SDK event handlers,
  // registered once per start(), always see current values.
  const finalRef = useRef(onFinalSegment);
  const interimRef = useRef(onInterim);
  finalRef.current = onFinalSegment;
  interimRef.current = onInterim;

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
  }, [releaseMedia]);

  useEffect(() => teardown, [teardown]);

  const start = useCallback(() => {
    if (!enabled || transcriberRef.current) return;
    setError(null);
    void (async () => {
      try {
        const { token, endpoint, language } = await api.stt.getToken();
        // Lazy-loaded so the ~450KB SDK only ships when capture is used.
        const sdk = await import("microsoft-cognitiveservices-speech-sdk");
        const speechConfig = sdk.SpeechConfig.fromEndpoint(new URL(endpoint));
        speechConfig.authorizationToken = token;
        speechConfig.speechRecognitionLanguage =
          lang ||
          language ||
          (typeof navigator !== "undefined" ? navigator.language : "en-US");

        // Open the selected input device (the loopback carrying meeting audio).
        const primary = await navigator.mediaDevices.getUserMedia({
          audio: deviceId ? { deviceId: { exact: deviceId } } : true,
        });
        const streams: MediaStream[] = [primary];

        // Optionally mix in the default microphone so both sides of a hybrid
        // meeting (in-room + remote) are transcribed.
        let feed = primary;
        if (captureMic && deviceId) {
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
          if (e.errorDetails) setError(e.errorDetails);
          teardown();
        };

        transcriberRef.current = transcriber;
        transcriber.startTranscribingAsync(
          () => {
            startMsRef.current = Date.now();
            setListening(true);
          },
          (err) => {
            setError(typeof err === "string" ? err : "Could not start capture");
            teardown();
          },
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not reach the speech service");
        teardown();
      }
    })();
  }, [enabled, lang, deviceId, captureMic, teardown]);

  const stop = useCallback(() => teardown(), [teardown]);

  return { supported: enabled, listening, error, start, stop };
}
