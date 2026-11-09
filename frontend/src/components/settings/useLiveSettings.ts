import { useState } from "react";
import type { SettingsSection } from "./settingsSection";

/** Live tab: the meeting assistant switch, its analysis model and retention. */
export function useLiveSettings() {
  const [liveEnabled, setLiveEnabled] = useState(true);
  const [liveFastModel, setLiveFastModel] = useState("");
  const [liveReasoningEffort, setLiveReasoningEffort] = useState("");
  const [liveTranscriptRetentionDays, setLiveTranscriptRetentionDays] = useState(7);
  const section: SettingsSection = {
    hydrate: (s) => {
      setLiveEnabled(s.live_enabled);
      setLiveFastModel(s.live_fast_model);
      setLiveReasoningEffort(s.live_reasoning_effort);
      setLiveTranscriptRetentionDays(s.live_transcript_retention_days);
    },
    payload: () => ({
      live_enabled: liveEnabled,
      live_fast_model: liveFastModel,
      live_reasoning_effort: liveReasoningEffort,
      live_transcript_retention_days: liveTranscriptRetentionDays,
    }),
    snapshot: [liveEnabled, liveFastModel, liveReasoningEffort, liveTranscriptRetentionDays],
  };
  return {
    liveEnabled, setLiveEnabled,
    liveFastModel, setLiveFastModel,
    liveReasoningEffort, setLiveReasoningEffort,
    liveTranscriptRetentionDays, setLiveTranscriptRetentionDays,
    section,
  };
}

export type LiveSettings = ReturnType<typeof useLiveSettings>;
