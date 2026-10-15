import { useCallback, useEffect, useRef, useState } from "react";
import { postSSE } from "./sse";
import { createQuestionGate, type QuestionGate } from "./liveQuestions";

export interface LiveSuggestion {
  id: number;
  speakerLabel: string | null;
  question: string;
  text: string;
  status: "streaming" | "done";
}

/**
 * Suggested answers for a recording Live session: feed final segments in, get a
 * streaming suggestion out when another participant asks a question. The server
 * saves each finished suggestion to the session chat; this only drives the
 * transient preview card.
 */
export function useLiveSuggestions({
  sessionId,
  enabled,
  meLabels,
}: {
  sessionId: number;
  enabled: boolean;
  meLabels: string[];
}) {
  const [suggestion, setSuggestion] = useState<LiveSuggestion | null>(null);
  // Suggestions saved to the chat since the user last looked at it.
  const [unseen, setUnseen] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const gateRef = useRef<QuestionGate | null>(null);
  const seqRef = useRef(0);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const meRef = useRef(meLabels);
  meRef.current = meLabels;

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const run = useCallback(
    (question: string, speakerLabel: string | null) => {
      if (!enabledRef.current) return;
      cancel();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const id = ++seqRef.current;
      setSuggestion({ id, speakerLabel, question, text: "", status: "streaming" });
      void (async () => {
        let none = false;
        try {
          await postSSE(
            `/api/live/${sessionId}/suggest`,
            { question, speaker_label: speakerLabel },
            {
              signal: ctrl.signal,
              onEvent: ({ event, data }) => {
                if (seqRef.current !== id) return;
                if (event === "token") {
                  const { content } = JSON.parse(data) as { content: string };
                  setSuggestion((s) => (s && s.id === id ? { ...s, text: s.text + content } : s));
                } else if (event === "none" || event === "error") {
                  none = true;
                } else if (event === "done") {
                  setSuggestion((s) => (s && s.id === id ? { ...s, status: "done" } : s));
                  setUnseen((n) => n + 1);
                }
              },
            },
          );
        } catch {
          none = true;
        }
        // A suggestion that was aborted, errored, or judged unnecessary leaves
        // no card; an aborted one was already replaced by a newer request.
        if (none && seqRef.current === id) setSuggestion(null);
      })();
    },
    [sessionId, cancel],
  );

  useEffect(() => {
    const gate = createQuestionGate({
      onQuestion: run,
      onOwnSpeech: () => {
        cancel();
        seqRef.current += 1;
        setSuggestion(null);
      },
    });
    gateRef.current = gate;
    return () => {
      gate.dispose();
      gateRef.current = null;
      cancel();
    };
  }, [run, cancel]);

  useEffect(() => {
    if (!enabled) {
      cancel();
      seqRef.current += 1;
      setSuggestion(null);
    }
  }, [enabled, cancel]);

  /** Feed one final transcript segment (scoped raw speaker label). */
  const push = useCallback((text: string, speakerLabel: string | null) => {
    if (!enabledRef.current) return;
    const isMe = speakerLabel != null && meRef.current.includes(speakerLabel);
    gateRef.current?.push(text, speakerLabel, isMe);
  }, []);

  const dismiss = useCallback(() => {
    cancel();
    seqRef.current += 1;
    setSuggestion(null);
  }, [cancel]);

  return { suggestion, push, dismiss, unseen, clearUnseen: () => setUnseen(0) };
}
