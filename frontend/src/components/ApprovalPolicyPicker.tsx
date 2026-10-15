import { useMemo } from "react";
import { ShieldCheck } from "lucide-react";
import { CONVERSATION_APPROVAL_POLICIES } from "../lib/approvalPolicies";
import { useSettings } from "../lib/settingsStore";
import type { ConversationApprovalPolicy } from "../lib/types";
import { Select } from "./Select";

interface Props {
  /** `null` inherits the global default. */
  value: ConversationApprovalPolicy | null;
  onChange: (value: ConversationApprovalPolicy | null) => void;
  disabled?: boolean;
  subject: "topic" | "chat";
}

export function ApprovalPolicyPicker({ value, onChange, disabled, subject }: Props) {
  const settings = useSettings();
  const globalLabel = useMemo(() => {
    const found = CONVERSATION_APPROVAL_POLICIES.find(
      (p) => p.value === (settings?.conversation_approval_policy ?? "autonomous"),
    );
    return found ? found.label.split("—")[0].trim() : "";
  }, [settings?.conversation_approval_policy]);

  return (
    <section>
      <label className="flex items-center gap-1.5 text-xs text-muted mb-1">
        <ShieldCheck size={13} />
        Tool approval
      </label>
      <Select
        fullWidth
        size="sm"
        ariaLabel={`Tool approval policy for this ${subject}`}
        disabled={disabled}
        value={value ?? ""}
        onChange={(v) => onChange(v === "" ? null : (v as ConversationApprovalPolicy))}
        options={[
          {
            value: "",
            label: `Inherit global default${globalLabel ? ` — ${globalLabel}` : ""}`,
          },
          ...CONVERSATION_APPROVAL_POLICIES.map((p) => ({ value: p.value, label: p.label })),
        ]}
      />
      <p className="text-[11px] text-muted mt-1 leading-relaxed">
        {value
          ? CONVERSATION_APPROVAL_POLICIES.find((p) => p.value === value)?.hint
          : "Falls back to the global default set in Settings → Chat. Applies from the next tool call."}
      </p>
    </section>
  );
}
