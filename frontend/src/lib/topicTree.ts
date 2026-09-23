import type { TopicNode } from "./types";

// Pure lookups over the app-wide topic tree (every collection, not just the
// one on screen), shared by routing, unread totals, notifications and the
// breadcrumb / topic-link headers.

function indexTree(nodes: TopicNode[]): Map<number, TopicNode> {
  const byId = new Map<number, TopicNode>();
  const walk = (level: TopicNode[]): void => {
    for (const n of level) {
      byId.set(n.id, n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return byId;
}

/** Ancestor → self slug chain for a topic, using the loaded tree. */
export function topicSlugPath(tree: TopicNode[], topicId: number): string[] {
  const byId = indexTree(tree);
  const path: string[] = [];
  let cur: TopicNode | undefined = byId.get(topicId);
  while (cur) {
    path.unshift(cur.slug);
    cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined;
  }
  return path;
}

/** Ancestor chain (root → immediate parent, excluding self) for a topic. */
export function topicAncestors(tree: TopicNode[], topicId: number): TopicNode[] {
  const byId = indexTree(tree);
  const chain: TopicNode[] = [];
  let cur = byId.get(topicId);
  let parentId = cur?.parent_id ?? null;
  while (parentId != null) {
    cur = byId.get(parentId);
    if (!cur) break;
    chain.unshift(cur);
    parentId = cur.parent_id ?? null;
  }
  return chain;
}

/** Sum unread counts across the whole topic tree (recursively). */
export function totalUnread(nodes: TopicNode[]): number {
  let n = 0;
  for (const node of nodes) {
    n += node.unread_count ?? 0;
    if (node.children?.length) n += totalUnread(node.children);
  }
  return n;
}

/** Find a topic's title anywhere in the tree (for notification text). */
export function findTitle(nodes: TopicNode[], topicId: number): string | null {
  for (const node of nodes) {
    if (node.id === topicId) return node.title;
    if (node.children?.length) {
      const hit = findTitle(node.children, topicId);
      if (hit) return hit;
    }
  }
  return null;
}

export function findNode(nodes: TopicNode[], topicId: number): TopicNode | null {
  for (const node of nodes) {
    if (node.id === topicId) return node;
    if (node.children?.length) {
      const hit = findNode(node.children, topicId);
      if (hit) return hit;
    }
  }
  return null;
}
