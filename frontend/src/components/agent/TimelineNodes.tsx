import type { ReactNode } from "react";
import { Activity, Loader2 } from "lucide-react";
import { CATEGORY_STYLE } from "../AgentTimeline";
import { formatTimestamp } from "../MessageMeta";
import { ReasoningDisclosure } from "../ReasoningDisclosure";

/** Who sent a prompt: the user's GitHub persona, or a fallback. */
export interface Persona {
  name: string;
  avatarUrl: string | null;
}

/** A node's round badge: the sender's avatar when there is one, else the category icon. */
export function NodeAvatar({
  user,
  marker,
  icon,
}: {
  user?: Persona | null;
  marker: string;
  icon: ReactNode;
}) {
  if (user?.avatarUrl) {
    return (
      <img
        src={user.avatarUrl}
        alt={user.name}
        className="h-6 w-6 shrink-0 rounded-full border border-sky-500/40 object-cover"
      />
    );
  }
  return (
    <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${marker}`}>
      {icon}
    </span>
  );
}

/** Long prompts are clipped so they can't flood the timeline. */
export const clip = (text: string): string => (text.length > 1500 ? `${text.slice(0, 1500)}…` : text);

// Thinking with no message of its own to sit on: a round that went straight to
// a tool call, or the thinking the model is streaming right now.
export function ThinkingNode({ reasoning, live = false }: { reasoning: string; live?: boolean }) {
  return (
    <div className={`w-full max-w-xl rounded-lg border px-2.5 py-1.5 ${CATEGORY_STYLE.reasoning.box}`}>
      <ReasoningDisclosure reasoning={reasoning} live={live} />
    </div>
  );
}

// Optimistic echo of a just-sent prompt. Rendered immediately on send — before
// the backend has recorded the real user turn — so it's obvious the message
// left the composer even while the session spins the turn up. Styled like the
// real user node but muted, with a "Sending…" spinner; it's swapped out for the
// real event the moment it lands.
export function PendingUserBubble({ text, user }: { text: string; user?: Persona }) {
  const style = CATEGORY_STYLE.user;
  return (
    <div
      className={`relative w-full max-w-xl rounded-lg border p-2.5 opacity-70 ${style.box}`}
      aria-busy="true"
    >
      <div className="flex items-center gap-2">
        <NodeAvatar user={user} marker={style.marker} icon={style.icon} />
        <span className="text-[11px] font-semibold">{user?.name ?? "You"}</span>
        <span className="ml-auto flex items-center gap-1 text-[10px] text-muted">
          <Loader2 size={11} className="animate-spin" />
          Sending…
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">{clip(text)}</p>
    </div>
  );
}

// A milestone marker lifted onto the workflow spine: PROGRESS heartbeats are
// pulled out of the raw prose and rendered as compact, iconified timeline nodes
// so the mission's trajectory reads at a glance instead of hiding as plain-text
// directive lines. The terminal OBJECTIVE_COMPLETE is *not* a spine node — it's
// folded into the final answer bubble (see MessageNode) so the completion and
// the answer are one and the same.
export function MissionMilestone({
  progress,
  at,
}: {
  progress?: { value: number; label: string | null } | null;
  at?: string | null;
}) {
  const when = formatTimestamp(at);
  if (!progress) return null;
  const pct = Math.max(0, Math.min(100, Math.round(progress.value)));
  return (
    <div className="my-1 flex w-full justify-center">
      <div className="inline-flex max-w-xl items-center gap-2 rounded-full border border-violet-500/30 bg-violet-500/10 px-3 py-1 text-[11px] text-violet-800 dark:text-violet-200">
        <Activity size={13} className="shrink-0" />
        <span className="font-semibold tabular-nums">{pct}%</span>
        {progress.label && (
          <span className="truncate text-violet-700/80 dark:text-violet-300/80">{progress.label}</span>
        )}
        {when && <span className="shrink-0 text-violet-700/60 dark:text-violet-300/60">{when}</span>}
      </div>
    </div>
  );
}
