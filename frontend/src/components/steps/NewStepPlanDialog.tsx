import { useState } from "react";
import { Loader2 } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
import type { StepPlan } from "../../lib/types";
import { Modal } from "../Modal";
import { BTN_PRIMARY, BTN_SECONDARY, ErrorBanner, INPUT } from "./StepUi";

/** "New step plan" from pasted text. Proposing steps is a model call, so it can take a while. */
export function NewStepPlanDialog({
  onClose,
  onCreated,
  onFailed,
}: {
  onClose: () => void;
  onCreated: (plan: StepPlan) => void;
  /** The plan exists server-side but the request failed (model error): refresh the list. */
  onFailed: () => void;
}) {
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [propose, setPropose] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(): Promise<void> {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      onCreated(
        await api.stepPlans.create({
          source_text: text.trim(),
          title: title.trim() || undefined,
          propose,
        }),
      );
    } catch (err) {
      onFailed();
      setError(
        apiErrorMessage(err, "Could not create the step plan.") +
          (propose ? " The plan may have been created without suggestions: check the list." : ""),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      onClose={busy ? undefined : onClose}
      closeOnEscape={!busy}
      padded
      labelledBy="new-step-plan-title"
      panelClassName="w-full max-w-2xl rounded-2xl border border-border bg-bg p-5 shadow-xl"
    >
      <h2 id="new-step-plan-title" className="text-lg font-semibold">
        New step plan
      </h2>
      <p className="mt-1 text-sm text-muted">
        Paste the ask (an email, a brief…). It will be split into steps you can freely edit.
      </p>
      <label className="mt-4 block text-xs font-medium text-muted">
        Title (optional)
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={255}
          disabled={busy}
          className={`${INPUT} mt-1`}
          placeholder="Defaults to the first line of the ask"
        />
      </label>
      <label className="mt-3 block text-xs font-medium text-muted">
        The ask
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          rows={10}
          className={`${INPUT} mt-1 resize-y`}
          placeholder="Paste the customer email or the list of requests…"
        />
      </label>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={propose}
          onChange={(e) => setPropose(e.target.checked)}
          disabled={busy}
        />
        Let the model propose the steps
      </label>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        </div>
      )}
      {busy && propose && (
        <p className="mt-3 flex items-center gap-2 text-xs text-muted" role="status">
          <Loader2 size={14} className="animate-spin" /> The model is reading the ask and proposing
          steps — this can take a moment…
        </p>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" onClick={onClose} disabled={busy} className={BTN_SECONDARY}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy || !text.trim()}
          className={BTN_PRIMARY}
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          {busy ? "Creating…" : "Create plan"}
        </button>
      </div>
    </Modal>
  );
}
