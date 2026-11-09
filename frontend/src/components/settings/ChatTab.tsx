import { notificationsSupported, requestNotificationPermission } from "../../lib/notifications";
import { playCompletionSound } from "../../lib/sound";
import type { ConversationApprovalPolicy } from "../../lib/types";
import { ApprovalRulesSection } from "../ApprovalRulesSection";
import { Select } from "../Select";
import { CONVERSATION_APPROVAL_POLICIES } from "../../lib/approvalPolicies";
import type { SettingsPanelState } from "./useSettingsPanelState";

export function ChatTab({ s }: { s: SettingsPanelState }) {
  const {
    models,
    chatAutonameEnabled,
    setChatAutonameEnabled,
    chatAutonameModel,
    setChatAutonameModel,
    showChatStats,
    setShowChatStats,
    notificationsEnabled,
    setNotificationsEnabled,
    completionSoundEnabled,
    setCompletionSoundEnabled,
    maxToolRounds,
    setMaxToolRounds,
    scheduledApprovalTimeout,
    setScheduledApprovalTimeout,
    topicApprovalPolicy,
    setTopicApprovalPolicy,
    chatApprovalPolicy,
    setChatApprovalPolicy,
  } = s;
  return (
    <>
      <section>
        <h3 className="text-sm font-medium mb-2">Interface</h3>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showChatStats}
            onChange={(e) => setShowChatStats(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">
              Show conversation stats sidebar
            </span>
            <span className="block text-[11px] text-muted">
              Displays token usage and context-window occupancy next
              to each chat. Stats are always collected; this only
              controls the panel's visibility.
            </span>
          </span>
        </label>
      </section>

      <section>
        <h3 className="text-sm font-medium mb-2">Auto-naming</h3>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={chatAutonameEnabled}
            onChange={(e) => setChatAutonameEnabled(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">
              Name new chats from their first message
            </span>
            <span className="block text-[11px] text-muted">
              Replaces the "New chat" placeholder with a short title
              derived from what you asked, so the sidebar stays
              readable. Runs alongside the answer and never delays it.
              Renaming a chat yourself always wins. Use{" "}
              <code>/suggest-name</code> to re-name any conversation
              later.
            </span>
          </span>
        </label>

        <label className="block text-xs text-muted mt-4 mb-1">
          Naming model
        </label>
        <Select
          value={chatAutonameModel}
          onChange={setChatAutonameModel}
          disabled={!chatAutonameEnabled}
          ariaLabel="Chat auto-naming model"
          fullWidth
          options={[
            { value: "", label: "Use default chat model" },
            ...models.map((m) => ({ value: m.id, label: m.name })),
          ]}
        />
        <p className="text-[11px] text-muted mt-1">
          Naming is a one-line request, so a small fast model is
          usually the better choice here.
        </p>
      </section>

      <section>
        <h3 className="text-sm font-medium mb-2">Notifications</h3>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={notificationsEnabled}
            onChange={async (e) => {
              const on = e.target.checked;
              if (on) {
                const perm = await requestNotificationPermission();
                if (perm !== "granted") {
                  setNotificationsEnabled(false);
                  return;
                }
              }
              setNotificationsEnabled(on);
            }}
            disabled={!notificationsSupported()}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">
              Notify when a reply is ready
            </span>
            <span className="block text-[11px] text-muted">
              {notificationsSupported()
                ? "Shows a browser notification when an assistant turn (including scheduled tasks) finishes while the Precursor window isn't focused. The unread count always appears in the tab title regardless of this setting."
                : "Your browser doesn't support notifications."}
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2 cursor-pointer mt-3">
          <input
            type="checkbox"
            checked={completionSoundEnabled}
            onChange={(e) => {
              const on = e.target.checked;
              // Previews the sound, and the click unlocks audio.
              if (on) playCompletionSound();
              setCompletionSoundEnabled(on);
            }}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">
              Play a sound when a reply finishes
            </span>
            <span className="block text-[11px] text-muted">
              A short, quiet pop when a reply you asked for in a topic
              or chat completes. Off by default.
            </span>
          </span>
        </label>
      </section>

      <section>
        <h3 className="text-sm font-medium mb-2">Tools</h3>
        <label className="block text-xs text-muted mb-1">
          Max tool-call rounds per message
        </label>
        <input
          type="number"
          min={1}
          max={1000}
          value={maxToolRounds}
          onChange={(e) =>
            setMaxToolRounds(
              Math.max(1, Math.min(1000, Number(e.target.value) || 1)),
            )
          }
          className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
        <p className="text-[11px] text-muted mt-1">
          Hard ceiling on the number of tool-call iterations the
          assistant may chain before the stream aborts. Increase for
          multi-step agents (e.g. browser automation); decrease to
          cut runaway loops short. Range: 1–1000.
        </p>
        <label className="block text-xs text-muted mb-1 mt-4">
          Default tool approval — topics
        </label>
        <Select
          fullWidth
          size="sm"
          ariaLabel="Default tool approval policy for topics"
          value={topicApprovalPolicy}
          onChange={(v) => setTopicApprovalPolicy(v as ConversationApprovalPolicy)}
          options={CONVERSATION_APPROVAL_POLICIES.map((p) => ({
            value: p.value,
            label: p.label,
          }))}
        />
        <p className="text-[11px] text-muted mt-1">
          {CONVERSATION_APPROVAL_POLICIES.find((p) => p.value === topicApprovalPolicy)?.hint}
           Each topic can change it from the shield pill in its composer.
        </p>
        <label className="block text-xs text-muted mb-1 mt-4">
          Default tool approval — chats
        </label>
        <Select
          fullWidth
          size="sm"
          ariaLabel="Default tool approval policy for chats"
          value={chatApprovalPolicy}
          onChange={(v) => setChatApprovalPolicy(v as ConversationApprovalPolicy)}
          options={CONVERSATION_APPROVAL_POLICIES.map((p) => ({
            value: p.value,
            label: p.label,
          }))}
        />
        <p className="text-[11px] text-muted mt-1">
          {CONVERSATION_APPROVAL_POLICIES.find((p) => p.value === chatApprovalPolicy)?.hint}
           Each chat can change it from the shield pill in its composer.
        </p>
        <label className="block text-xs text-muted mb-1 mt-4">
          Unattended approval timeout (minutes)
        </label>
        <input
          type="number"
          min={0}
          value={scheduledApprovalTimeout}
          onChange={(e) =>
            setScheduledApprovalTimeout(Math.max(0, Math.floor(Number(e.target.value) || 0)))
          }
          className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
        <p className="text-[11px] text-muted mt-1">
          When a scheduled run needs an approval nobody is there to give, it waits this
          long and is then denied, and the model is told nobody answered. 0 waits
          forever. A topic's schedule can override it.
        </p>
      </section>

      <section>
        <h3 className="text-sm font-medium mb-2">Approval rules</h3>
        <ApprovalRulesSection owner={{ kind: "global" }} />
        <p className="text-[11px] text-muted mt-2">
          Rules for a single conversation live in its settings.
        </p>
      </section>
    </>
  );
}
