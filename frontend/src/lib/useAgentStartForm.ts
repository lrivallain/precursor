import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useDictation } from "./useDictation";
import { useResizableHeight } from "./useResizableHeight";
import type { AgentApprovalPolicy } from "./types";

export interface AgentStartForm {
  task: string;
  setTask: Dispatch<SetStateAction<string>>;
  topicId: number | null;
  setTopicId: (id: number | null) => void;
  /** Assistant Role the new agent is created with. Null = the default role. */
  role: string | null;
  setRole: (role: string | null) => void;
  /**
   * Autonomy opt-in: when on, the agent runs a goal loop toward the objective
   * and pauses only by exception (default off).
   */
  autonomy: boolean;
  toggleAutonomy: () => void;
  maxSteps: number;
  setMaxSteps: (n: number) => void;
  /** Per-agent override; "" (empty) inherits the global default set in Settings. */
  approvalPolicy: AgentApprovalPolicy | "";
  setApprovalPolicy: (p: AgentApprovalPolicy | "") => void;
  /**
   * Launch the agent immediately (default) or park it in the `waiting` state,
   * armed for a later trigger (parent completion, webhook, or "Start now").
   */
  start: boolean;
  toggleStart: () => void;
  /** Clear the per-task fields after a successful create. */
  reset: () => void;
}

/**
 * The new-agent form's fields. Held by the view rather than the form, so a
 * half-written task survives opening an agent and coming back.
 */
export function useAgentStartForm(agentId: number | null, draftTopicId: number | null | undefined): AgentStartForm {
  const [task, setTask] = useState("");
  const [topicId, setTopicId] = useState<number | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [autonomy, setAutonomy] = useState(false);
  const [maxSteps, setMaxSteps] = useState(12);
  const [approvalPolicy, setApprovalPolicy] = useState<AgentApprovalPolicy | "">("");
  const [start, setStart] = useState(true);

  // Preselect the originating topic when opened via "/agent" from a topic
  // (only while no session is selected).
  useEffect(() => {
    if (agentId == null && draftTopicId != null) setTopicId(draftTopicId);
  }, [agentId, draftTopicId]);

  const reset = useCallback(() => {
    setTask("");
    setTopicId(null);
    setRole(null);
    setStart(true);
  }, []);

  return {
    task,
    setTask,
    topicId,
    setTopicId,
    role,
    setRole,
    autonomy,
    toggleAutonomy: () => setAutonomy((v) => !v),
    maxSteps,
    setMaxSteps,
    approvalPolicy,
    setApprovalPolicy,
    start,
    toggleStart: () => setStart((s) => !s),
    reset,
  };
}

export interface AgentComposerInfra {
  speech: ReturnType<typeof useDictation>["speech"];
  interimText: string;
  height: number;
  onResizeStart: ReturnType<typeof useResizableHeight>["onMouseDown"];
}

/**
 * Shared composer infrastructure (same as topics/chats): resizable height and
 * dictation. Only one composer is mounted at a time (the start form *or* the
 * follow-up box), so a single setup serves both — dictation targets whichever
 * is active.
 */
export function useAgentComposerInfra(
  hasSelected: boolean,
  setTask: Dispatch<SetStateAction<string>>,
  setFollowUp: Dispatch<SetStateAction<string>>,
): AgentComposerInfra {
  // Read at dictation time, not when the speech session was set up.
  const selectedRef = useRef(false);
  useEffect(() => {
    selectedRef.current = hasSelected;
  }, [hasSelected]);
  const { height, onMouseDown } = useResizableHeight({
    storageKey: "precursor:agent-composer:height",
    defaultHeight: 56,
    min: 40,
    max: 480,
  });
  const { interimText, speech } = useDictation((update) =>
    selectedRef.current ? setFollowUp(update) : setTask(update),
  );
  return { speech, interimText, height, onResizeStart: onMouseDown };
}
