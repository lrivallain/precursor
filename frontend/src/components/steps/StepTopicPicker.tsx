import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Collection, Topic } from "../../lib/types";
import { TopicPicker } from "../TopicPicker";

/** The app-wide topic lookup, loading its own list: where a plan is linked to a topic. */
export function StepTopicPicker({
  value,
  onChange,
  disabled,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
}) {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  useEffect(() => {
    void api.topics.list().then(setTopics).catch(() => setTopics([]));
    void api.collections.list().then(setCollections).catch(() => setCollections([]));
  }, []);
  return (
    <TopicPicker topics={topics} value={value} onChange={onChange} disabled={disabled} collections={collections} />
  );
}
