import type { ConversationApprovalPolicy } from "./types";

// Ordered most → least cautious. Wording for the conversation-level policy:
// the agent-mode list in AgentsSettings describes a different runtime.
export const CONVERSATION_APPROVAL_POLICIES: {
  value: ConversationApprovalPolicy;
  label: string;
  hint: string;
}[] = [
  {
    value: "manual",
    label: "Manual — ask before every tool call",
    hint: "Every MCP tool call and every Precursor write (workspace files, notes, memories…) waits for your approval.",
  },
  {
    value: "balanced",
    label: "Balanced — ask before changes",
    hint: "Read-only tools run on their own; anything that changes something, or is not known to be read-only, waits for your approval. The model can also ask first when it is unsure.",
  },
  {
    value: "autonomous",
    label: "Autonomous — never ask",
    hint: "Every tool call runs on its own. The model can still pause to ask you when it judges an action needs your go-ahead.",
  },
];
