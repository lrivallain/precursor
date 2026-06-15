import { useEffect, useState } from "react";
import { Download, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { skillsStore, useSkills } from "../lib/skillsStore";
import type { Skill } from "../lib/types";

export function SkillsTab() {
  const skills = useSkills();
  const [editing, setEditing] = useState<Skill | "new" | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void skillsStore.ensureLoaded();
  }, []);

  async function handleDelete(skill: Skill): Promise<void> {
    if (!confirm(`Delete skill "/${skill.name}"?`)) return;
    setBusyId(skill.id);
    setError(null);
    try {
      await api.deleteSkill(skill.id);
      await skillsStore.load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-muted">
          Skills are reusable prompts invoked as <code>/name</code> in chat.
          The instructions are prepended to your input before it's sent.
        </p>
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs"
        >
          <Plus size={12} /> New
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
          {error}
        </div>
      )}

      {skills.length === 0 ? (
        <div className="border border-dashed border-border rounded p-4 text-xs text-muted text-center space-y-1">
          <Sparkles size={18} className="mx-auto text-muted" />
          <div className="text-sm text-text">No skills yet</div>
          <p>Create one to add a custom slash command.</p>
        </div>
      ) : (
        <ul className="space-y-1.5">
          {skills.map((s) => (
            <li
              key={s.id}
              className="border border-border rounded px-2 py-1.5 flex items-center gap-2"
            >
              <div className="flex-1 min-w-0">
                <div className="text-sm font-mono text-accent truncate">
                  /{s.name}
                </div>
                {s.description && (
                  <div className="text-[11px] text-muted truncate">
                    {s.description}
                  </div>
                )}
              </div>
              <a
                href={api.skillExportUrl(s.id)}
                data-tooltip="Export as .SKILL.md"
                aria-label="Export as .SKILL.md"
                className="p-1 rounded hover:bg-surface text-muted hover:text-text"
              >
                <Download size={14} />
              </a>
              <button
                type="button"
                onClick={() => setEditing(s)}
                className="p-1 rounded hover:bg-surface text-muted hover:text-text"
                data-tooltip="Edit"
                aria-label="Edit skill"
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                onClick={() => void handleDelete(s)}
                disabled={busyId === s.id}
                className="p-1 rounded hover:bg-surface text-muted hover:text-red-500 disabled:opacity-40"
                data-tooltip="Delete"
                aria-label="Delete skill"
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <SkillEditor
          skill={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await skillsStore.load();
            setEditing(null);
          }}
        />
      )}
    </section>
  );
}

interface EditorProps {
  skill: Skill | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}

function SkillEditor({ skill, onClose, onSaved }: EditorProps) {
  const [name, setName] = useState(skill?.name ?? "");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [instructions, setInstructions] = useState(skill?.instructions ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      if (skill) {
        await api.updateSkill(skill.id, {
          name: name.trim(),
          description: description.trim() || null,
          instructions,
        });
      } else {
        await api.createSkill({
          name: name.trim(),
          description: description.trim() || null,
          instructions,
        });
      }
      await onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(560px,100%)] max-h-[90vh] bg-bg border border-border rounded shadow-lg flex flex-col">
        <header className="flex items-center justify-between px-4 h-10 border-b border-border">
          <h3 className="font-semibold text-sm">
            {skill ? `Edit /${skill.name}` : "New skill"}
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {error && (
            <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs text-muted mb-1">
              Command name
            </label>
            <div className="flex items-center gap-1">
              <span className="text-sm text-muted">/</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="to-en"
                className="flex-1 bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent"
              />
            </div>
            <p className="text-[10px] text-muted mt-1">
              Lowercase letters, digits, hyphens. Must start with a letter.
            </p>
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">
              Description (optional)
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Translate text to English"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">
              Instructions
            </label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={10}
              placeholder="Translate text or word to English.&#10;&#10;If multiple words exist: provide the usage context to help selection."
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent"
            />
            <p className="text-[10px] text-muted mt-1">
              These instructions are prepended to whatever the user types
              after the command name.
            </p>
          </div>
        </div>

        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={saving || !name.trim() || !instructions.trim()}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving…" : skill ? "Save" : "Create"}
          </button>
        </footer>
      </div>
    </div>
  );
}
