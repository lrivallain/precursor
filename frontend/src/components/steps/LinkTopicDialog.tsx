import { useState } from "react";
import { Loader2 } from "lucide-react";
import type { StepPlan } from "../../lib/types";
import { Modal } from "../Modal";
import { StepTopicPicker } from "./StepTopicPicker";
import { BTN_PRIMARY, BTN_SECONDARY } from "./StepUi";

/** Link a plan to a topic (or change it) once it exists. */
export function LinkTopicDialog({
  plan,
  onClose,
  onLink,
}: {
  plan: StepPlan;
  onClose: () => void;
  onLink: (topicId: number) => Promise<boolean>;
}) {
  const [topicId, setTopicId] = useState<number | null>(plan.source_topic_id);
  const [busy, setBusy] = useState(false);

  async function link(): Promise<void> {
    if (topicId === null || busy) return;
    setBusy(true);
    try {
      if (await onLink(topicId)) onClose();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      onClose={busy ? undefined : onClose}
      closeOnEscape={!busy}
      padded
      labelledBy="link-topic-title"
      panelClassName="w-full max-w-md rounded-2xl border border-border bg-bg p-5 shadow-xl"
    >
      <h2 id="link-topic-title" className="text-lg font-semibold">
        {plan.source_topic_id ? "Change the topic" : "Link a topic"}
      </h2>
      <p className="mt-1 text-sm text-muted">
        The topic&rsquo;s discussion becomes background for the model, and the final output can be sent
        back to it. A note in the topic points to this plan.
      </p>
      <div className="mt-4">
        <StepTopicPicker value={topicId} onChange={setTopicId} disabled={busy} />
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" onClick={onClose} disabled={busy} className={BTN_SECONDARY}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void link()}
          disabled={busy || topicId === null || topicId === plan.source_topic_id}
          className={BTN_PRIMARY}
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          Link
        </button>
      </div>
    </Modal>
  );
}
