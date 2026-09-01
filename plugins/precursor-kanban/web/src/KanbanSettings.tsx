import { useState } from "react";
import { Eye, Plus, SquareKanban, Trash2 } from "lucide-react";
import { usePluginSettings } from "@precursor/host";
import {
  HIDDEN_KEY,
  MAX_SOURCES,
  SETTINGS_DEFAULTS,
  SOURCES_KEY,
  isValidSource,
  readList,
} from "./sources";
import type { KanbanSettingsBlob } from "./sources";

/**
 * Settings → Plugins → Kanban.
 *
 * Day-to-day board management lives on the board itself: the header "+" adds a
 * project, and right-clicking a row hides it or stops tracking its source. This
 * page is the full picture behind that, and the only place two things are
 * reachable:
 *
 * - a **broken** source — renamed, revoked, made private — resolves to no boards
 *   at all, so it has no row on the board to right-click;
 * - a **hidden** board is by definition not in the picker, so unhiding has to
 *   happen from a list of what's hidden.
 */
export function KanbanSettings() {
  const { value, setValue, save, saving, error, dirty } = usePluginSettings<KanbanSettingsBlob>(
    "kanban",
    SETTINGS_DEFAULTS,
  );
  const [draft, setDraft] = useState("");

  if (value === null) return <div className="text-sm text-muted">Loading…</div>;

  // Read defensively: the blob is whatever was last stored, and a corrupt list
  // here would take the whole settings modal down with it.
  const sources = readList(value, SOURCES_KEY);
  const hidden = readList(value, HIDDEN_KEY);
  const trimmed = draft.trim();
  const full = sources.length >= MAX_SOURCES;
  const malformed = trimmed.length > 0 && !isValidSource(trimmed);
  const canAdd = trimmed.length > 0 && !malformed && !full;

  function add() {
    const entry = draft.trim();
    if (!canAdd) return;
    if (sources.includes(entry)) {
      setDraft("");
      return;
    }
    setValue({ ...value!, [SOURCES_KEY]: [...sources, entry] });
    setDraft("");
  }

  function remove(entry: string) {
    setValue({ ...value!, [SOURCES_KEY]: sources.filter((s) => s !== entry) });
  }

  function unhide(entry: string) {
    setValue({ ...value!, [HIDDEN_KEY]: hidden.filter((h) => h !== entry) });
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold">Kanban</h3>
        <p className="text-xs text-muted">
          The board already lists every project owned by the account behind your
          configured GitHub repository. Add more accounts or individual projects
          here — a customer's roadmap, another org you contribute to. You can also
          do this from the board's <strong>+</strong> button.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <label className="text-xs font-medium" htmlFor="kanban-source">
          Extra project sources
        </label>
        <div className="flex gap-2">
          <input
            id="kanban-source"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            placeholder="acme-corp, acme-corp#4, or a project URL"
            className="min-w-0 flex-1 rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={add}
            disabled={!canAdd}
            className="flex shrink-0 items-center gap-1 rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            <Plus size={13} />
            Add
          </button>
        </div>
        {malformed && (
          <p className="text-[11px] text-red-500">
            Not a GitHub account or project. Use <code>acme-corp</code>,{" "}
            <code>acme-corp#4</code>, or a project URL.
          </p>
        )}
        {full && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            That's the maximum of {MAX_SOURCES} sources. Remove one to add another.
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

      {sources.length > 0 && (
        <ul className="flex flex-col gap-1">
          {sources.map((entry) => (
            <li
              key={entry}
              className="flex items-center gap-2 rounded border border-border bg-surface/60 px-2.5 py-1.5"
            >
              <SquareKanban size={13} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{entry}</span>
              <button
                type="button"
                onClick={() => remove(entry)}
                className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-red-500"
                aria-label={`Remove ${entry}`}
                data-tooltip="Remove"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {hidden.length > 0 && (
        <div className="flex flex-col gap-2">
          <label className="text-xs font-medium" htmlFor="kanban-hidden">
            Hidden projects
          </label>
          <p className="text-[11px] text-muted">
            Boards you've hidden from the picker. Hiding never stops a source
            being tracked, so bringing one back doesn't need re-adding anything.
          </p>
          <ul id="kanban-hidden" className="flex flex-col gap-1">
            {hidden.map((entry) => (
              <li
                key={entry}
                className="flex items-center gap-2 rounded border border-border bg-surface/60 px-2.5 py-1.5"
              >
                <SquareKanban size={13} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{entry}</span>
                <button
                  type="button"
                  onClick={() => unhide(entry)}
                  className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-accent"
                  aria-label={`Show ${entry} again`}
                  data-tooltip="Show again"
                >
                  <Eye size={13} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || saving}
          className="rounded border border-accent/30 bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {dirty && !saving && (
          <span className="text-[11px] text-muted">
            Reopen the board to pick up the change.
          </span>
        )}
      </div>
    </section>
  );
}
