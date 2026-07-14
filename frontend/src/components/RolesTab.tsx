import { useEffect, useState } from "react";
import { Drama, Lock, Pencil, Plus, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { rolesStore, useRoles } from "../lib/rolesStore";
import type { Role } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

export function RolesTab() {
  const confirmAction = useConfirm();
  const roles = useRoles();
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void rolesStore.ensureLoaded();
  }, []);

  async function handleDelete(role: Role): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete role "${role.name}"? Discussions using it revert to default.`,
        confirmLabel: "Delete role",
        variant: "danger",
      }))
    )
      return;
    setBusyId(role.id);
    setError(null);
    try {
      await api.roles.remove(role.id);
      await rolesStore.load();
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
          Roles are personas applied to a whole discussion. The system prompt is
          re-applied to every reply until you switch roles.
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

      {roles.length === 0 ? (
        <div className="border border-dashed border-border rounded p-4 text-xs text-muted text-center space-y-1">
          <Drama size={18} className="mx-auto text-muted" />
          <div className="text-sm text-text">No roles yet</div>
          <p>Create one to give the assistant a persistent persona.</p>
        </div>
      ) : (
        <ul className="space-y-1.5">
          {roles.map((r) => (
            <li
              key={r.id}
              className="border border-border rounded px-2 py-1.5 flex items-center gap-2"
            >
              <div className="flex-1 min-w-0">
                <div className="text-sm text-text truncate flex items-center gap-1">
                  {r.is_default && <Lock size={11} className="text-muted shrink-0" />}
                  {r.name}
                </div>
                <div className="text-[11px] text-muted truncate">
                  {r.system_prompt
                    ? r.system_prompt
                    : r.is_default
                      ? "No persona — the assistant behaves normally."
                      : "Empty prompt"}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setEditing(r)}
                className="p-1 rounded hover:bg-surface text-muted hover:text-text"
                data-tooltip="Edit"
                aria-label="Edit role"
              >
                <Pencil size={14} />
              </button>
              {!r.is_default && (
                <button
                  type="button"
                  onClick={() => void handleDelete(r)}
                  disabled={busyId === r.id}
                  className="p-1 rounded hover:bg-surface text-muted hover:text-red-500 disabled:opacity-40"
                  data-tooltip="Delete"
                  aria-label="Delete role"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <RoleEditor
          role={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await rolesStore.load();
            setEditing(null);
          }}
        />
      )}
    </section>
  );
}

interface EditorProps {
  role: Role | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}

function RoleEditor({ role, onClose, onSaved }: EditorProps) {
  const isDefault = role?.is_default ?? false;
  const [name, setName] = useState(role?.name ?? "");
  const [systemPrompt, setSystemPrompt] = useState(role?.system_prompt ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      if (role) {
        await api.roles.update(role.id, {
          ...(isDefault ? {} : { name: name.trim() }),
          system_prompt: systemPrompt,
        });
      } else {
        await api.roles.create({
          name: name.trim(),
          system_prompt: systemPrompt,
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
            {role ? `Edit ${role.name}` : "New role"}
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
            <label className="block text-xs text-muted mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Senior Code Reviewer"
              disabled={isDefault}
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-50"
            />
            {isDefault && (
              <p className="text-[10px] text-muted mt-1">
                The default role can't be renamed or deleted.
              </p>
            )}
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">System prompt</label>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={10}
              placeholder="You are a meticulous senior engineer. Review for correctness, edge cases, and security. Be direct and concise."
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent"
            />
            <p className="text-[10px] text-muted mt-1">
              Injected into the system context for every reply in discussions
              assigned this role.
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
            disabled={saving || (!isDefault && !name.trim())}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving…" : role ? "Save" : "Create"}
          </button>
        </footer>
      </div>
    </div>
  );
}
