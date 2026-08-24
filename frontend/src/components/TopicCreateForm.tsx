import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { Select } from "./Select";
import { RefineTextarea } from "./RefineTextarea";
import { useSettings } from "../lib/settingsStore";
import type { Collection, Topic, TopicNode } from "../lib/types";

interface Props {
  /**
   * The app-wide topic tree. The form scopes the parent picker to the selected
   * collection itself, so it needs every root, not a pre-filtered slice.
   */
  tree: TopicNode[];
  initialParentId?: number | null;
  /** Collection to preselect — the one the sidebar is showing. */
  collectionId?: number | null;
  onCreated: (topic: Topic) => void;
  /** When provided, renders a Cancel button (e.g. inside the modal wrapper). */
  onCancel?: () => void;
  submitLabel?: string;
  autoFocus?: boolean;
}

/**
 * The topic creation form, decoupled from any chrome so it can serve both the
 * modal (tree "+ child") and the inline start surfaces on the home page and the
 * Topics empty state — matching how chat/live/agent expose their start forms.
 */
export function TopicCreateForm({
  tree,
  initialParentId = null,
  collectionId = null,
  onCreated,
  onCancel,
  submitLabel = "Create topic",
  autoFocus = true,
}: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [parentId, setParentId] = useState<number | "">(
    initialParentId === null ? "" : initialParentId,
  );
  const [collections, setCollections] = useState<Collection[]>([]);
  // Only set once the user picks one. The effective value is derived below, so
  // it can't snapshot a prop that hasn't resolved yet on a cold load.
  const [collectionOverride, setCollectionOverride] = useState<number | null>(null);
  const [repo, setRepo] = useState("");
  const [issueNumber, setIssueNumber] = useState("");
  const [createLinkedIssue, setCreateLinkedIssue] = useState(false);
  const [defaultRepo, setDefaultRepo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settings = useSettings();
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;

  // A subtree lives in exactly one collection, so a chosen parent decides —
  // matching what the server does. Otherwise it's the user's pick, falling back
  // to the collection the sidebar is showing.
  const parentCollectionId = useMemo(
    () => (parentId === "" ? null : (findNode(tree, parentId)?.collection_id ?? null)),
    [tree, parentId],
  );
  const effectiveCollectionId: number | "" =
    parentCollectionId ?? collectionOverride ?? collectionId ?? "";

  // Only this collection's topics can parent the new one; membership cascades,
  // so filtering the roots covers their whole subtrees.
  const parentOptions = useMemo(
    () =>
      flatten(
        effectiveCollectionId === ""
          ? tree
          : tree.filter((n) => n.collection_id === effectiveCollectionId),
      ),
    [tree, effectiveCollectionId],
  );

  useEffect(() => {
    void (async () => {
      try {
        const s = await api.settings.get();
        setDefaultRepo(s.github_repo);
      } catch {
        /* settings optional */
      }
      try {
        setCollections(await api.collections.list());
      } catch {
        /* collections optional */
      }
    })();
  }, []);

  async function submit(): Promise<void> {
    const trimmed = title.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.topics.create({
        title: trimmed,
        description: description.trim() || null,
        parent_id: parentId === "" ? null : parentId,
        // A sub-topic inherits its parent's collection server-side; only a
        // top-level topic names one.
        collection_id:
          parentId === "" ? (effectiveCollectionId === "" ? null : effectiveCollectionId) : null,
        github_repo: repo.trim() || null,
        github_issue_number: createLinkedIssue
          ? null
          : issueNumber.trim()
            ? Number(issueNumber.trim())
            : null,
        create_linked_issue: createLinkedIssue,
      });
      onCreated(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs text-muted mb-1">Title</label>
        <input
          autoFocus={autoFocus}
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
        <label className="block text-xs text-muted mb-1">
          Description (optional)
        </label>
        <RefineTextarea
          value={description}
          onValueChange={setDescription}
          refineKind="description"
          rows={3}
          placeholder="Context the assistant should keep in mind"
          className="w-full resize-y bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
      </div>

      {collections.length > 1 && (
        <div>
          <label className="block text-xs text-muted mb-1">Collection</label>
          <Select
            value={effectiveCollectionId === "" ? "" : String(effectiveCollectionId)}
            onChange={(v) => {
              setCollectionOverride(v === "" ? null : Number(v));
              // The parent list is scoped to the collection, so a parent from
              // the previous one would silently drag the topic back into it.
              setParentId("");
            }}
            ariaLabel="Collection"
            fullWidth
            options={collections.map((c) => ({
              value: String(c.id),
              label: c.name,
            }))}
          />
          <p className="mt-1 text-xs text-muted">
            {parentId === ""
              ? "Where this topic lands in the sidebar."
              : "Sub-topics inherit their parent's collection."}
          </p>
        </div>
      )}

      <div>
        <label className="block text-xs text-muted mb-1">Parent topic</label>
        <Select
          value={parentId === "" ? "" : String(parentId)}
          onChange={(v) => setParentId(v === "" ? "" : Number(v))}
          ariaLabel="Parent topic"
          fullWidth
          options={[
            { value: "", label: "— top level —" },
            ...parentOptions.map((opt) => ({
              value: String(opt.id),
              label: `${"\u00A0".repeat(opt.depth * 2)}${opt.title}`,
            })),
          ]}
        />
      </div>

      {issueAssociationsEnabled && (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={createLinkedIssue}
              onChange={(e) => setCreateLinkedIssue(e.target.checked)}
              className="accent-accent"
            />
            Create a linked GitHub issue
          </label>

          {createLinkedIssue ? (
            <div>
              <label className="block text-xs text-muted mb-1">GitHub repo</label>
              <input
                type="text"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder={defaultRepo || "owner/name"}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              />
              <p className="mt-1 text-xs text-muted">
                Opens an issue titled{" "}
                <span className="font-mono">
                  [parent topics] {title.trim() || "title"}
                </span>{" "}
                with the description as its body, then links it to this topic.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-[1fr_120px] gap-2">
              <div>
                <label className="block text-xs text-muted mb-1">
                  GitHub repo (optional)
                </label>
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
        </div>
      )}

      {error && <p className="text-xs text-red-500">{error}</p>}

      <div className="flex justify-end gap-2">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          onClick={() => void submit()}
          disabled={!title.trim() || submitting}
          className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
        >
          {submitting ? "Creating…" : submitLabel}
        </button>
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

function findNode(tree: TopicNode[], id: number): TopicNode | null {
  for (const node of tree) {
    if (node.id === id) return node;
    const hit = findNode(node.children, id);
    if (hit) return hit;
  }
  return null;
}
