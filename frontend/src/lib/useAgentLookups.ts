import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Collection, Me, Topic } from "./types";

/**
 * What the agents surface looks up once the feature is on: topics and
 * collections for the new-agent form, and the user's GitHub persona (name +
 * avatar) for their prompts, with a fallback when not signed in.
 */
export function useAgentLookups(enabled: boolean): {
  topics: Topic[];
  collections: Collection[];
  persona: { name: string; avatarUrl: string | null };
} {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    if (!enabled) return;
    void api.topics.list().then(setTopics).catch(() => setTopics([]));
    void api.collections.list().then(setCollections).catch(() => setCollections([]));
    void api.me.get().then(setMe).catch(() => setMe(null));
  }, [enabled]);
  const persona = useMemo(
    () => ({
      name: me?.github?.name || me?.github?.login || "You",
      avatarUrl: me?.github?.avatar_url ?? null,
    }),
    [me],
  );
  return { topics, collections, persona };
}
