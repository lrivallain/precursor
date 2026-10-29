import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import { CONVERSATION_APPROVAL_POLICIES } from "../lib/approvalPolicies";
import { useSettings } from "../lib/settingsStore";
import type { ConversationApprovalPolicy } from "../lib/types";
import { ComposerSelectMenu } from "./ComposerSelectMenu";

const INHERIT = "__inherit__";

interface Props {
  kind: "topic" | "chat";
  id: number;
  /** `null` inherits the global default for this kind of conversation. */
  policy: ConversationApprovalPolicy | null | undefined;
  onUpdated: () => void;
}

const shortLabel = (value: ConversationApprovalPolicy): string =>
  CONVERSATION_APPROVAL_POLICIES.find((p) => p.value === value)?.label.split("—")[0].trim() ?? value;

/** Composer-toolbar pill that sets how this conversation's tool calls are approved. */
export function ComposerApprovalControl({ kind, id, policy, onUpdated }: Props) {
  const settings = useSettings();
  const [value, setValue] = useState<ConversationApprovalPolicy | null>(policy ?? null);
  const [saving, setSaving] = useState(false);

  useEffect(() => setValue(policy ?? null), [policy, id]);

  const inherited: ConversationApprovalPolicy =
    (kind === "chat" ? settings?.chat_approval_policy : settings?.topic_approval_policy) ?? "autonomous";
  const effective = value ?? inherited;
  const current = CONVERSATION_APPROVAL_POLICIES.find((p) => p.value === effective);

  async function choose(next: ConversationApprovalPolicy | null): Promise<void> {
    if (saving || next === value) return;
    const previous = value;
    setValue(next);
    setSaving(true);
    try {
      if (kind === "chat") await api.chats.update(id, { approval_policy: next });
      else await api.topics.update(id, { approval_policy: next });
      onUpdated();
    } catch {
      setValue(previous);
    } finally {
      setSaving(false);
    }
  }

  return (
    <ComposerSelectMenu
      ariaLabel="Tool approval policy"
      tooltip={`Tool approval — ${current?.hint ?? ""}`}
      triggerLabel={shortLabel(effective)}
      icon={<ShieldCheck size={13} className="shrink-0 opacity-70" />}
      value={value ?? INHERIT}
      scrollToSelected={false}
      menuMinWidthClass="min-w-[16rem]"
      disabled={saving}
      groups={[
        {
          options: [
            {
              value: INHERIT,
              label: `Default — ${shortLabel(inherited)}`,
              description: `Follows the ${kind} default in Settings → Chat.`,
            },
            ...CONVERSATION_APPROVAL_POLICIES.map((p) => ({
              value: p.value,
              label: p.label,
              description: p.hint,
            })),
          ],
        },
      ]}
      onSelect={(v) => void choose(v === INHERIT ? null : (v as ConversationApprovalPolicy))}
    />
  );
}
