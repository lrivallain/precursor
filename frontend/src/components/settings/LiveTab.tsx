import { Select } from "../Select";
import { NumberInput } from "./NumberInput";
import type { LLMModel } from "../../lib/types";
import type { LiveSettings } from "./useLiveSettings";

export function LiveTab({ s, models }: { s: LiveSettings; models: LLMModel[] }) {
  const {
    liveFastModel,
    setLiveFastModel,
    liveReasoningEffort,
    setLiveReasoningEffort,
    liveEnabled,
    setLiveEnabled,
    liveTranscriptRetentionDays,
    setLiveTranscriptRetentionDays,
  } = s;
  return (
    <section>
      <p className="text-sm text-muted mb-3">
        The <strong>Live</strong> section records a meeting, transcribes
        it with speaker labels, surfaces live insights, and can attach a
        summary to a topic. Transcription uses your{" "}
        <strong>Azure Speech</strong> resource (configured under
        Speech-to-text).
      </p>

      <label className="flex items-center gap-2 mb-4 text-sm">
        <input
          type="checkbox"
          checked={liveEnabled}
          onChange={(e) => setLiveEnabled(e.target.checked)}
          className="accent-accent"
        />
        Enable the Live meeting assistant section
      </label>

      <div className="border-t border-border pt-4">
        <h3 className="text-sm font-medium">Analysis model</h3>
        <p className="text-[11px] text-muted mt-1 mb-3">
          Model for the rolling live insights and Q&amp;A. A fast model
          keeps analysis snappy; leave on the default to reuse your chat
          model. (Summaries use the default chat model for quality.)
        </p>

        <label className="block text-xs text-muted mb-1">Fast model</label>
        <Select
          value={liveFastModel}
          onChange={setLiveFastModel}
          disabled={!liveEnabled}
          ariaLabel="Live analysis fast model"
          fullWidth
          options={[
            { value: "", label: "Use default chat model" },
            ...models.map((m) => ({ value: m.id, label: m.name })),
          ]}
        />

        <label className="block text-xs text-muted mt-4 mb-1">
          Reasoning effort
        </label>
        <Select
          value={liveReasoningEffort}
          onChange={setLiveReasoningEffort}
          disabled={!liveEnabled}
          ariaLabel="Live analysis reasoning effort"
          fullWidth
          options={[
            { value: "", label: "Auto / off (fastest)" },
            { value: "low", label: "Low" },
            { value: "medium", label: "Medium" },
            { value: "high", label: "High" },
          ]}
        />
        <p className="text-[11px] text-muted mt-1">
          Live analysis favours speed — keep this low. Only applies to
          reasoning-capable models.
        </p>
      </div>

      <div className="border-t border-border pt-4 mt-4">
        <h3 className="text-sm font-medium">Transcript retention</h3>
        <p className="text-[11px] text-muted mt-1 mb-3">
          Automatically delete a session's transcript this many days
          after it ends, to keep the database small. Only the
          transcript is removed — the session's insights, notes and
          summary are kept. 0 = keep forever.
        </p>
        <label className="block text-xs text-muted mb-1">
          Delete transcript after (days)
        </label>
        <NumberInput
          value={liveTranscriptRetentionDays}
          min={0}
          max={3650}
          onCommit={setLiveTranscriptRetentionDays}
        />
      </div>
    </section>
  );
}
