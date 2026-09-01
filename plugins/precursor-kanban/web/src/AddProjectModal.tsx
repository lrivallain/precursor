/**
 * "Add a project" — the board's own create flow.
 *
 * The header "+" is core's (every section's sits in the same place, wearing the
 * section's tint); this is what it means for the kanban board. Tracking another
 * account or project used to be a trip to Settings → Plugins → Kanban, which is
 * a long way round for the section's primary create action.
 *
 * The settings panel is still the place a *broken* entry gets fixed: a source
 * that has been renamed, revoked or made private resolves to no boards at all,
 * so it has no row here to right-click.
 */

import { useEffect, useRef, useState } from "react";
import { Modal, apiErrorMessage } from "@precursor/host";
import { kanbanSettings } from "./api";
import { MAX_SOURCES, SOURCES_KEY, isValidSource, readList } from "./sources";

interface Props {
  onClose: () => void;
  /** Re-list the boards once a source has been stored. */
  onAdded: () => void;
}

export function AddProjectModal({ onClose, onAdded }: Props) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // The click that opened this dialog *was* "I want to add a project", so make
  // the caret land where the user is already typing.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // The cap is the server's, so read the stored list rather than guessing.
  // Failure here is not fatal: the add itself re-reads and enforces it anyway.
  useEffect(() => {
    let cancelled = false;
    void kanbanSettings
      .read()
      .then((blob) => {
        if (!cancelled) setFull(readList(blob, SOURCES_KEY).length >= MAX_SOURCES);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const trimmed = draft.trim();
  const malformed = trimmed.length > 0 && !isValidSource(trimmed);
  const canAdd = trimmed.length > 0 && !malformed && !full && !saving;

  async function submit(): Promise<void> {
    if (!canAdd) return;
    setSaving(true);
    setError(null);
    try {
      await kanbanSettings.addSource(trimmed);
      onAdded();
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e, "Failed to add the project"));
      setSaving(false);
    }
  }

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      labelledBy="kanban-add-title"
      panelClassName="w-full max-w-lg rounded-lg border border-border bg-bg p-5 shadow-xl"
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="flex flex-col gap-1">
          <h2 id="kanban-add-title" className="text-sm font-semibold">
            Add a project
          </h2>
          <p className="text-xs text-muted">
            The board already lists every project owned by the account behind your
            configured GitHub repository. Add another account or a single project
            — a customer's roadmap, another org you contribute to.
          </p>
        </div>

        {error && (
          <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-500">
            {error}
          </div>
        )}

        <div className="flex flex-col gap-2">
          <label className="text-xs font-medium" htmlFor="kanban-add-source">
            Account or project
          </label>
          <input
            id="kanban-add-source"
            ref={inputRef}
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="acme-corp, acme-corp#4, or a project URL"
            className="w-full rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          {malformed && (
            <p className="text-[11px] text-red-500">
              Not a GitHub account or project. Use <code>acme-corp</code>,{" "}
              <code>acme-corp#4</code>, or a project URL.
            </p>
          )}
          {full && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              That's the maximum of {MAX_SOURCES} sources. Remove one in Settings →
              Plugins → Kanban to add another.
            </p>
          )}
          <p className="text-[11px] text-muted">
            An <strong>account</strong> (<code>acme-corp</code>) adds every open
            project it owns. A <strong>single project</strong> (
            <code>acme-corp#4</code>, or the project's GitHub URL) adds just that
            one. You need access to it, and a token with the <code>project</code>{" "}
            scope.
          </p>
        </div>

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-border px-3 py-1.5 text-xs font-medium hover:bg-surface"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canAdd}
            className="rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {saving ? "Adding…" : "Add project"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
