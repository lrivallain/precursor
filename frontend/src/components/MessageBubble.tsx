import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { MessageRole } from "../lib/types";

interface Props {
  role: MessageRole;
  content: string;
  pending?: boolean;
}

const roleLabel: Record<MessageRole, string> = {
  user: "You",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
};

export function MessageBubble({ role, content, pending }: Props) {
  const isUser = role === "user";
  return (
    <div
      className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"} max-w-3xl mx-auto`}
    >
      <div className="text-[11px] uppercase tracking-wide text-muted">{roleLabel[role]}</div>
      <div
        className={`px-3 py-2 rounded-lg border border-border ${
          isUser ? "bg-accent/10" : "bg-surface"
        } max-w-full`}
      >
        <div className="markdown text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || "\u200B"}</ReactMarkdown>
        </div>
        {pending && <div className="mt-1 text-[11px] text-muted italic">streaming...</div>}
      </div>
    </div>
  );
}
