import { useState } from "react";
import { Plus, SquareKanban, Trash2 } from "lucide-react";
import { usePluginSettings } from "@precursor/host";

/** Mirrors `precursor_kanban.sources.SOURCES_KEY`. */
const SOURCES_KEY = "project_sources";
const MAX_SOURCES = 20;

// Mirrors `precursor_kanban.sources.parse_source`. The server drops anything it
// can't read, so a bad entry is never fatal — but silently storing one the board
// then ignores is a confusing way to find that out, so reject it here.
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/;
const PROJECT_URL =
  /^https?:\/\/(?:www\.)?github\.com\/(?:orgs|users)\/([^/]+)\/projects\/\d+/i;
const OWNER_URL = /^https?:\/\/(?:www\.)?github\.com\/([^/?#]+)\/?$/i;

/** Whether the server will understand `raw` as a project source. */
function isValidSource(raw: string): boolean {
  const text = raw.trim();
  if (!text) return false;
  const project = PROJECT_URL.exec(text);
  if (project) return LOGIN.test(project[1]);
  const owner = OWNER_URL.exec(text);
  if (owner) return LOGIN.test(owner[1]);
  if (text.includes("#")) {
    const [login, number] = text.split("#", 2);
    return LOGIN.test(login.trim()) && /^\d+$/.test(number.trim());
  }
  return LOGIN.test(text);
}

interface KanbanSettings extends Record<string, unknown> {
  project_sources: string[];
}

const DEFAULTS: KanbanSettings = { project_sources: [] };

/**
 * Settings → Plugins → Kanban.
 *
 * The board lists the projects owned by whoever owns the repo in Settings →
 * GitHub. That is a sensible default and a poor ceiling: the board you care
 * about often belongs to someone else. This adds extra accounts or individual
 * projects on top.
 */
export function KanbanSettings() {
  const { value, setValue, save, saving, error, dirty } = usePluginSettings<KanbanSettings>(
    "kanban",
    DEFAULTS,
  );
  const [draft, setDraft] = useState("");

  if (value === null) return <div className="text-sm text-muted">Loading…</div>;

  const sources = value[SOURCES_KEY] ?? [];
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

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold">Kanban</h3>
        <p className="text-xs text-muted">
          The board already lists every project owned by the account behind your
          configured GitHub repository. Add more accounts or individual projects
          here — a customer's roadmap, another org you contribute to.
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
