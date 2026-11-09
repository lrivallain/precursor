import { describe, expect, it } from "vitest";
import { findNode, findTitle, topicAncestors, topicSlugPath, totalUnread } from "./topicTree";
import type { TopicNode } from "./types";

function node(
  id: number,
  slug: string,
  parent_id: number | null,
  unread_count: number,
  children: TopicNode[] = [],
): TopicNode {
  return { id, slug, title: slug.toUpperCase(), parent_id, unread_count, children } as unknown as TopicNode;
}

const tree = [
  node(1, "work", null, 2, [node(2, "project", 1, 1, [node(3, "task", 2, 4)])]),
  node(4, "home", null, 0),
];

describe("topicTree", () => {
  it("builds the slug path from the root", () => {
    expect(topicSlugPath(tree, 3)).toEqual(["work", "project", "task"]);
    expect(topicSlugPath(tree, 99)).toEqual([]);
  });

  it("lists ancestors without the topic itself", () => {
    expect(topicAncestors(tree, 3).map((n) => n.id)).toEqual([1, 2]);
    expect(topicAncestors(tree, 1)).toEqual([]);
  });

  it("sums unread counts across the whole tree", () => {
    expect(totalUnread(tree)).toBe(7);
  });

  it("finds nodes and titles at any depth", () => {
    expect(findNode(tree, 3)?.slug).toBe("task");
    expect(findTitle(tree, 2)).toBe("PROJECT");
    expect(findTitle(tree, 99)).toBeNull();
  });
});
