import { useMemo, type RefObject } from "react";
import { matchAgentSlashCommands, type SlashCommand } from "../../lib/commands";
import type { AgentComposerInfra } from "../../lib/useAgentStartForm";
import type { RewindController } from "../../lib/useRewind";
import type { TurnTimeline } from "../../lib/useTurnTimeline";
import type { AgentPane } from "../AgentResults";
import { Composer } from "../Composer";
import { ComposerModelControls } from "../ComposerModelControls";
import { RewindBar } from "../RewindBar";
import { RoleSelector } from "../RoleSelector";

function placeholder(turnActive: boolean, sending: boolean, pane: AgentPane): string {
  if (turnActive) return "Agent is working… use Stop to interrupt";
  if (sending) return "Sending…";
  return pane === "result" ? "Ask for changes to this result…" : "Send a follow-up message…";
}

/**
 * Follow-up: always visible. While a turn is in flight the input is disabled
 * and the send button becomes a Stop control, matching the topic/chat composer
 * pattern.
 */
export function AgentFollowUpComposer({
  wrapRef,
  pane,
  turnTimeline,
  rewind,
  scrollRef,
  turnActive,
  sending,
  runtimeReady,
  value,
  onChange,
  onSend,
  onStop,
  userHistory,
  composer,
  role,
  onSetRole,
  roleOpen,
  onRoleOpenChange,
}: {
  /** Lets a raised question jump the human straight to the answer box. */
  wrapRef: RefObject<HTMLDivElement | null>;
  pane: AgentPane;
  turnTimeline: TurnTimeline;
  rewind: RewindController;
  scrollRef: RefObject<HTMLDivElement | null>;
  turnActive: boolean;
  /**
   * The send request is still in flight: the turn hasn't reported active yet,
   * so lock the input (no double send) without flashing a Stop control.
   */
  sending: boolean;
  runtimeReady: boolean;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  userHistory: string[];
  composer: AgentComposerInfra;
  role: string | null;
  onSetRole: (roleName: string | null) => void;
  roleOpen: boolean;
  onRoleOpenChange: (open: boolean) => void;
}) {
  // Agents support only the system-handled slash commands (/rename, /clear,
  // /archive); skills and every other builtin are disabled here. The backend
  // rejects anything else.
  const suggestions = useMemo<SlashCommand[]>(() => matchAgentSlashCommands(value) ?? [], [value]);
  return (
    <div ref={wrapRef} className="shrink-0 space-y-2 border-t border-border px-5 py-3 pb-safe">
      {pane === "activity" && (
        <RewindBar timeline={turnTimeline} rewind={rewind} streaming={turnActive} scrollRef={scrollRef} />
      )}
      <Composer
        value={value}
        onChange={onChange}
        onSend={onSend}
        onStop={onStop}
        streaming={turnActive}
        disabled={!runtimeReady || turnActive || sending || Boolean(rewind.busy)}
        suggestions={suggestions}
        userHistory={userHistory}
        speech={composer.speech}
        interimText={composer.interimText}
        height={composer.height}
        onResizeStart={composer.onResizeStart}
        placeholder={placeholder(turnActive, sending, pane)}
        toolbarStart={
          <>
            <ComposerModelControls variant="agents" />
            <RoleSelector value={role} onChange={onSetRole} open={roleOpen} onOpenChange={onRoleOpenChange} />
          </>
        }
      />
    </div>
  );
}
