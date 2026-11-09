import { useState } from "react";
import type { ConversationApprovalPolicy } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";

/** Chat tab: interface, auto-naming, notifications and tool-approval defaults. */
export function useChatSettings() {
  const [showChatStats, setShowChatStats] = useState(true);
  // Auto-naming a fresh chat from its first message, and the model that does it
  // ("" = the default chat model).
  const [chatAutonameEnabled, setChatAutonameEnabled] = useState(true);
  const [chatAutonameModel, setChatAutonameModel] = useState("");
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [completionSoundEnabled, setCompletionSoundEnabled] = useState(false);
  const [maxToolRounds, setMaxToolRounds] = useState(15);
  const [scheduledApprovalTimeout, setScheduledApprovalTimeout] = useState(60);
  const [topicApprovalPolicy, setTopicApprovalPolicy] = useState<ConversationApprovalPolicy>("autonomous");
  const [chatApprovalPolicy, setChatApprovalPolicy] = useState<ConversationApprovalPolicy>("autonomous");
  const section: SettingsSection = {
    hydrate: (s) => {
      setShowChatStats(s.show_chat_stats);
      setChatAutonameEnabled(s.chat_autoname_enabled);
      setChatAutonameModel(s.chat_autoname_model);
      setNotificationsEnabled(s.notifications_enabled);
      setCompletionSoundEnabled(s.completion_sound_enabled);
      setMaxToolRounds(s.max_tool_rounds);
      setScheduledApprovalTimeout(s.scheduled_approval_timeout_minutes ?? 60);
      setTopicApprovalPolicy(s.topic_approval_policy ?? "autonomous");
      setChatApprovalPolicy(s.chat_approval_policy ?? "autonomous");
    },
    payload: () => ({
      show_chat_stats: showChatStats,
      chat_autoname_enabled: chatAutonameEnabled,
      chat_autoname_model: chatAutonameModel,
      notifications_enabled: notificationsEnabled,
      completion_sound_enabled: completionSoundEnabled,
      max_tool_rounds: maxToolRounds,
      scheduled_approval_timeout_minutes: scheduledApprovalTimeout,
      topic_approval_policy: topicApprovalPolicy,
      chat_approval_policy: chatApprovalPolicy,
    }),
    snapshot: [
      showChatStats, chatAutonameEnabled, chatAutonameModel, notificationsEnabled,
      completionSoundEnabled, maxToolRounds, scheduledApprovalTimeout,
      topicApprovalPolicy, chatApprovalPolicy,
    ],
  };
  return {
    showChatStats, setShowChatStats,
    chatAutonameEnabled, setChatAutonameEnabled,
    chatAutonameModel, setChatAutonameModel,
    notificationsEnabled, setNotificationsEnabled,
    completionSoundEnabled, setCompletionSoundEnabled,
    maxToolRounds, setMaxToolRounds,
    scheduledApprovalTimeout, setScheduledApprovalTimeout,
    topicApprovalPolicy, setTopicApprovalPolicy,
    chatApprovalPolicy, setChatApprovalPolicy,
    section,
  };
}

export type ChatSettings = ReturnType<typeof useChatSettings>;
