import { useEffect, useState } from "react";
import { Layers, Lock, Pencil, Plus, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Collection, CollectionAccent } from "../lib/types";
import { COLLECTION_ACCENTS, collectionColor } from "../lib/collections";

interface Props {
  /** Notifies the app so the sidebar switcher and tree stay in sync. */
  onChanged?: () => void | Promise<void>;
}

export function CollectionsTab({ onChanged }: Props) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [editing, setEditing] = useState<Collection | "new" | null>(null);
  const [deleting, setDeleting] = useState<Collection | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    try {
      setCollections(await api.collections.list());
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function refresh(): Promise<void> {
    await load();
    await onChanged?.();
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-muted">
          Collections group topics so the sidebar shows one set at a time. Each can
          point at its own GitHub repository for linked issues; topics without an
          override inherit it.
        </p>
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs shrink-0"
        >
          <Plus size={12} /> New
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
          {error}
        </div>
      )}

      {collections.length === 0 ? (
        <div className="border border-dashed border-border rounded p-4 text-xs text-muted text-center space-y-1">
          <Layers size={18} className="mx-auto text-muted" />
          <div className="text-sm text-text">No collections yet</div>
        </div>
      ) : (
        <ul className="space-y-1.5">
          {collections.map((c) => {
            const color = collectionColor(c.accent);
            return (
              <li
                key={c.id}
                className="border border-border rounded px-2 py-1.5 flex items-center gap-2"
              >
                <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${color.dot}`} aria-hidden />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-text truncate flex items-center gap-1">
                    {c.is_default && <Lock size={11} className="text-muted shrink-0" />}
                    {c.name}
                    <span className="text-[11px] text-muted">
                      · {c.topic_count} topic{c.topic_count === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted truncate">
                    {c.github_repo ?? "Inherits the global GitHub repository"}
                    {c.description ? ` — ${c.description}` : ""}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setEditing(c)}
                  className="p-1 rounded hover:bg-surface text-muted hover:text-text"
                  data-tooltip="Edit"
                  aria-label={`Edit ${c.name}`}
                >
                  <Pencil size={14} />
                </button>
                {!c.is_default && (
                  <button
                    type="button"
                    onClick={() => setDeleting(c)}
                    className="p-1 rounded hover:bg-surface text-muted hover:text-red-500"
                    data-tooltip="Delete"
                    aria-label={`Delete ${c.name}`}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {editing && (
        <CollectionEditor
          collection={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            await refresh();
            setEditing(null);
          }}
        />
      )}

      {deleting && (
        <DeleteCollectionDialog
          collection={deleting}
          collections={collections}
          onClose={() => setDeleting(null)}
          onDeleted={async () => {
            await refresh();
            setDeleting(null);
          }}
        />
      )}
    </section>
  );
}

interface EditorProps {
  collection: Collection | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}

function CollectionEditor({ collection, onClose, onSaved }: EditorProps) {
  const isDefault = collection?.is_default ?? false;
  const [name, setName] = useState(collection?.name ?? "");
  const [description, setDescription] = useState(collection?.description ?? "");
  const [repo, setRepo] = useState(collection?.github_repo ?? "");
  const [accent, setAccent] = useState<CollectionAccent>(collection?.accent ?? "sky");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    const payload = {
      description: description.trim() || null,
      github_repo: repo.trim() || null,
      accent,
    };
    try {
      if (collection) {
        await api.collections.update(collection.id, {
          ...(isDefault ? {} : { name: name.trim() }),
          ...payload,
        });
      } else {
        await api.collections.create({ name: name.trim(), ...payload });
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
      <div className="w-[min(480px,100%)] max-h-[90vh] bg-bg border border-border rounded shadow-lg flex flex-col">
        <header className="flex items-center justify-between px-4 h-10 border-b border-border">
          <h3 className="font-semibold text-sm">
            {collection ? `Edit ${collection.name}` : "New collection"}
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

          <label className="block space-y-1">
            <span className="text-xs text-muted">Name</span>
            <input
              type="text"
              value={name}
              disabled={isDefault}
              onChange={(e) => setName(e.target.value)}
              placeholder="Work"
              className="w-full px-2 py-1.5 text-sm bg-surface border border-border rounded outline-none focus:border-accent disabled:opacity-50"
            />
            {isDefault && (
              <span className="text-[11px] text-muted">
                The default collection can't be renamed or deleted.
              </span>
            )}
          </label>

          <label className="block space-y-1">
            <span className="text-xs text-muted">Description</span>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              className="w-full px-2 py-1.5 text-sm bg-surface border border-border rounded outline-none focus:border-accent"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-xs text-muted">GitHub repository</span>
            <input
              type="text"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="owner/repo — defaults to the global setting"
              className="w-full px-2 py-1.5 text-sm bg-surface border border-border rounded outline-none focus:border-accent"
            />
            <span className="text-[11px] text-muted">
              Issues created from a topic in this collection land here unless the
              topic sets its own repository.
            </span>
          </label>

          <div className="space-y-1">
            <span className="text-xs text-muted">Accent</span>
            <div className="flex items-center gap-2">
              {COLLECTION_ACCENTS.map((key) => (
                <button
                  key={key}
                  type="button"
                  aria-label={key}
                  aria-pressed={accent === key}
                  onClick={() => setAccent(key)}
                  className={`w-5 h-5 rounded-full ${collectionColor(key).dot} ${
                    accent === key ? "ring-2 ring-offset-2 ring-offset-bg ring-accent" : ""
                  }`}
                />
              ))}
            </div>
          </div>
        </div>

        <footer className="flex items-center justify-end gap-2 px-4 h-12 border-t border-border">
          <button onClick={onClose} className="px-3 py-1 text-sm rounded hover:bg-surface">
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={saving || (!isDefault && !name.trim())}
            className="px-3 py-1 text-sm rounded bg-accent text-white disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}

interface DeleteProps {
  collection: Collection;
  collections: Collection[];
  onClose: () => void;
  onDeleted: () => Promise<void> | void;
}

/** Deleting never deletes topics — they move to a destination the user picks. */
function DeleteCollectionDialog({ collection, collections, onClose, onDeleted }: DeleteProps) {
  const targets = collections.filter((c) => c.id !== collection.id);
  const [destination, setDestination] = useState<number>(
    targets.find((c) => c.is_default)?.id ?? targets[0]?.id ?? 0,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.collections.remove(collection.id, destination || null);
      await onDeleted();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(420px,100%)] bg-bg border border-border rounded shadow-lg flex flex-col">
        <header className="flex items-center justify-between px-4 h-10 border-b border-border">
          <h3 className="font-semibold text-sm">Delete {collection.name}</h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={16} />
          </button>
        </header>

        <div className="p-4 space-y-3">
          {error && (
            <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
              {error}
            </div>
          )}
          <p className="text-xs text-muted">
            Its {collection.topic_count} topic{collection.topic_count === 1 ? "" : "s"} will
            move to another collection — nothing is deleted.
          </p>
          <label className="block space-y-1">
            <span className="text-xs text-muted">Move topics to</span>
            <select
              value={destination}
              onChange={(e) => setDestination(Number(e.target.value))}
              className="w-full px-2 py-1.5 text-sm bg-surface border border-border rounded outline-none focus:border-accent"
            >
              {targets.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <footer className="flex items-center justify-end gap-2 px-4 h-12 border-t border-border">
          <button onClick={onClose} className="px-3 py-1 text-sm rounded hover:bg-surface">
            Cancel
          </button>
          <button
            onClick={() => void remove()}
            disabled={busy || !destination}
            className="px-3 py-1 text-sm rounded bg-red-500 text-white disabled:opacity-40"
          >
            {busy ? "Deleting…" : "Delete collection"}
          </button>
        </footer>
      </div>
    </div>
  );
}
