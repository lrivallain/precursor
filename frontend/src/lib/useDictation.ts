import { useEffect, useState } from "react";
import { useSettings } from "./settingsStore";
import { useAzureSpeech } from "./useAzureSpeech";

export interface Dictation {
  /** Transient dictation text, shown until Azure finalises the chunk. */
  interimText: string;
  speech: ReturnType<typeof useAzureSpeech>;
}

/**
 * Live speech-to-text via Azure (when configured server-side) for a composer.
 * Each final chunk is appended to the draft through `updateDraft`, which is a
 * state setter or, for a surface with several drafts, routes to the active one.
 * The mic is hidden entirely when Azure isn't configured.
 */
export function useDictation(
  updateDraft: (update: (draft: string) => string) => void,
): Dictation {
  const settings = useSettings();
  const [interimText, setInterimText] = useState("");
  const speech = useAzureSpeech({
    onFinalChunk: (text) => {
      const chunk = text.trim();
      if (!chunk) return;
      updateDraft((d) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk));
      setInterimText("");
    },
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  // Drop any lingering interim text once dictation stops.
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  return { interimText, speech };
}
