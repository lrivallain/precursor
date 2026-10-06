import { useLayoutEffect, useRef, useState } from "react";
import { ArrowUpRight, ChevronDown, ChevronUp, Quote } from "lucide-react";
import { openTopic } from "../lib/sideChats";
import type { Chat } from "../lib/types";
import { Markdown } from "./Markdown";

// Collapsed height of the quoted reply; taller replies get a "Show more" toggle.
const COLLAPSED_PX = 160;

/**
 * The reply a side chat was started from, quoted above its transcript. It's a
 * frozen copy: it stays after the source turn is rewound or deleted, only the
 * "Open in topic" link goes away then.
 */
export function SideChatSeedCard({ chat }: { chat: Chat }) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const seed = chat.seed_content ?? "";

  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el) setOverflows(el.scrollHeight > COLLAPSED_PX + 8);
  }, [seed]);

  if (!seed) return null;
  const topicId = chat.parent_topic_id ?? null;
  return (
    <figure
      className="rounded-lg border border-accent/30 bg-accent/[0.04] px-3 py-2"
      data-testid="side-chat-seed"
    >
      <figcaption className="mb-1 flex items-center gap-1.5 text-xs text-muted">
        <Quote size={12} className="shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate">
          Started from a reply
          {chat.parent_topic_title ? (
            <>
              {" in "}
              <span className="font-medium text-fg">{chat.parent_topic_title}</span>
            </>
          ) : null}
        </span>
        {topicId != null && chat.parent_message_id != null && (
          <button
            type="button"
            onClick={() => openTopic(topicId, chat.parent_message_id)}
            className="flex shrink-0 items-center gap-0.5 rounded px-1 text-accent hover:bg-accent/10"
            data-tooltip="Open the topic at this reply"
          >
            Open in topic
            <ArrowUpRight size={12} />
          </button>
        )}
      </figcaption>
      <div
        ref={bodyRef}
        className="relative overflow-hidden"
        style={expanded ? undefined : { maxHeight: COLLAPSED_PX }}
      >
        <Markdown className="text-sm leading-relaxed">{seed}</Markdown>
        {!expanded && overflows && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-bg to-transparent" />
        )}
      </div>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 flex items-center gap-1 text-xs text-muted hover:text-fg"
          aria-expanded={expanded}
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </figure>
  );
}
