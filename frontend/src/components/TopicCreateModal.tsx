import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "../lib/api";
import { useSettings } from "../lib/settingsStore";
import type { Topic, TopicNode } from "../lib/types";

interface Props {
  initialParentId: number | null;
  tree: TopicNode[];
  onClose: () => void;
  onCreated: (topic: Topic) => void;
}

export function TopicCreateModal({ initialParentId, tree, onClose, onCreated }: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [parentId, setParentId] = useState<number | "">(
    initialParentId === null ? "" : initialParentId,
  );
  const [repo, setRepo] = useState("");
  const [issueNumber, setIssueNumber] = useState("");
  const [defaultRepo, setDefaultRepo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settings = useSettings();
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;

  useEffect(() => {
    void (async () => {
      try {
        const s = await api.getSettings();
        setDefaultRepo(s.github_repo);
      } catch {
        /* settings optional */
      }
    })();
  }, []);

  async function submit(): Promise<void> {
    const trimmed = title.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createTopic({
        title: trimmed,
        description: description.trim() || null,
        parent_id: parentId === "" ? null : parentId,
        github_repo: repo.trim() || null,
        github_issue_number: issueNumber.trim() ? Number(issueNumber.trim()) : null,
      });
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="w-[min(520px,100%)] bg-bg border border-border rounded-lg shadow-lg flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold">New topic</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="p-4 space-y-4">
          <div>
            <label className="block text-xs text-muted mb-1">Title</label>
            <input
              autoFocus
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
              }}
              placeholder="Short, descriptive title"
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">Description (optional)</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Context the assistant should keep in mind"
              className="w-full resize-none bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">Parent topic</label>
            <select
              value={parentId === "" ? "" : String(parentId)}
              onChange={(e) =>
                setParentId(e.target.value === "" ? "" : Number(e.target.value))
              }
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            >
              <option value="">— top level —</option>
              {flatten(tree).map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {"\u00A0".repeat(opt.depth * 2)}
                  {opt.title}
                </option>
              ))}
            </select>
          </div>

          {issueAssociationsEnabled && (
            <div className="grid grid-cols-[1fr_120px] gap-2">
              <div>
                <label className="block text-xs text-muted mb-1">GitHub repo (optional)</label>
                <input
                  type="text"
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  placeholder={defaultRepo || "owner/name"}
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Issue #</label>
                <input
                  type="number"
                  value={issueNumber}
                  onChange={(e) => setIssueNumber(e.target.value)}
                  placeholder="123"
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                />
              </div>
            </div>
          )}

          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>

        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void submit()}
            disabled={!title.trim() || submitting}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function flatten(
  tree: TopicNode[],
  depth = 0,
  out: { id: number; title: string; depth: number }[] = [],
): { id: number; title: string; depth: number }[] {
  for (const node of tree) {
    out.push({ id: node.id, title: node.title, depth });
    if (node.children.length) flatten(node.children, depth + 1, out);
  }
  return out;
}
