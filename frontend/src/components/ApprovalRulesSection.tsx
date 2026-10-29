import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import type {
  ApprovalRule,
  ApprovalRuleCreate,
  ApprovalRuleEffect,
  ArgClause,
} from "../lib/types";
import { Select } from "./Select";

export type RulesOwner = { kind: "global" } | { kind: "topic" | "chat"; id: number };

const EFFECTS = [
  { value: "allow", label: "Allow — run without asking" },
  { value: "ask", label: "Ask — always check with me" },
];

const OPS = [
  { value: "under", label: "is inside folder" },
  { value: "equals", label: "equals" },
  { value: "glob", label: "matches pattern" },
];

const OP_TEXT: Record<ArgClause["op"], string> = {
  under: "inside",
  equals: "=",
  glob: "matches",
};

export function describeClauses(clauses: ArgClause[]): string {
  return clauses.map((c) => `${c.arg} ${OP_TEXT[c.op]} ${c.value}`).join(" and ");
}

const inputCls =
  "w-full bg-surface border border-border rounded px-2 py-1 text-xs outline-none focus:border-accent";

function listParams(owner: RulesOwner): Parameters<typeof api.approvalRules.list>[0] {
  if (owner.kind === "topic") return { topicId: owner.id };
  if (owner.kind === "chat") return { chatId: owner.id };
  return undefined;
}

/**
 * Standing allow / ask rules, either the global ones or one conversation's.
 * Rules sit in front of the approval policy: a matching rule decides, anything
 * else falls back to the policy.
 */
export function ApprovalRulesSection({ owner }: { owner: RulesOwner }) {
  const [rules, setRules] = useState<ApprovalRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [effect, setEffect] = useState<ApprovalRuleEffect>("allow");
  const [server, setServer] = useState("*");
  const [tool, setTool] = useState("*");
  const [withArg, setWithArg] = useState(false);
  const [arg, setArg] = useState("path");
  const [op, setOp] = useState<ArgClause["op"]>("under");
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const ownerKey = owner.kind === "global" ? "global" : `${owner.kind}:${owner.id}`;

  const load = useCallback(async () => {
    try {
      setRules(await api.approvalRules.list(listParams(owner)));
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load the rules"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownerKey]);

  useEffect(() => {
    setRules(null);
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, fallback: string): Promise<boolean> {
    setError(null);
    try {
      await action();
      await load();
      return true;
    } catch (err) {
      setError(apiErrorMessage(err, fallback));
      return false;
    }
  }

  async function add(): Promise<void> {
    const body: ApprovalRuleCreate = {
      effect,
      server: server.trim() || "*",
      tool: tool.trim() || "*",
      arg_match: withArg && value.trim() ? [{ arg: arg.trim(), op, value: value.trim() }] : [],
      scope: owner.kind,
      topic_id: owner.kind === "topic" ? owner.id : null,
      chat_id: owner.kind === "chat" ? owner.id : null,
    };
    setSaving(true);
    const ok = await run(() => api.approvalRules.create(body), "Could not add the rule");
    setSaving(false);
    if (ok) {
      setAdding(false);
      setServer("*");
      setTool("*");
      setWithArg(false);
      setValue("");
    }
  }

  const where = owner.kind === "global" ? "every conversation" : "this conversation";

  return (
    <div>
      <p className="text-[11px] text-muted mb-2">
        Rules answer tool calls in {where} before the approval policy does. The first match wins,
        and “ask” beats “allow” at the same level. Calls no rule matches follow the policy.
      </p>
      {rules?.length === 0 && !adding && <p className="text-xs text-muted mb-2">No rules yet.</p>}
      <ul className="space-y-1.5 mb-2">
        {(rules ?? []).map((r) => (
          <li
            key={r.id}
            className={`flex items-start gap-2 rounded border border-border bg-surface px-2 py-1.5 ${
              r.enabled ? "" : "opacity-60"
            }`}
          >
            <input
              type="checkbox"
              checked={r.enabled}
              aria-label={`Rule ${r.id} enabled`}
              data-tooltip={r.enabled ? "Disable this rule" : "Enable this rule"}
              onChange={(e) =>
                void run(
                  () => api.approvalRules.update(r.id, { enabled: e.target.checked }),
                  "Could not update the rule",
                )
              }
              className="mt-1"
            />
            <div className="min-w-0 flex-1 text-xs">
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className={`rounded px-1.5 py-px text-[10px] font-semibold uppercase ${
                    r.effect === "allow"
                      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                      : "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                  }`}
                >
                  {r.effect}
                </span>
                <span className="font-mono break-all">
                  {r.server}
                  {" · "}
                  {r.tool}
                </span>
              </div>
              {r.arg_match.length > 0 && (
                <div className="mt-0.5 font-mono text-[11px] text-muted break-all">
                  when {describeClauses(r.arg_match)}
                </div>
              )}
              <div className="mt-0.5 text-[11px] text-muted">
                {r.hit_count === 0
                  ? "Never used"
                  : `Used ${r.hit_count} time${r.hit_count === 1 ? "" : "s"}${
                      r.last_used_at ? ` · last ${new Date(r.last_used_at).toLocaleString()}` : ""
                    }`}
                {r.note ? ` · ${r.note}` : ""}
              </div>
            </div>
            <button
              type="button"
              aria-label="Delete rule"
              data-tooltip="Delete rule"
              onClick={() =>
                void run(() => api.approvalRules.remove(r.id), "Could not delete the rule")
              }
              className="p-1 rounded text-muted hover:text-red-500 hover:bg-border/40"
            >
              <Trash2 size={13} />
            </button>
          </li>
        ))}
      </ul>

      {adding ? (
        <div className="space-y-2 rounded border border-border p-2">
          <Select
            fullWidth
            size="sm"
            ariaLabel="Rule effect"
            value={effect}
            onChange={(v) => setEffect(v as ApprovalRuleEffect)}
            options={EFFECTS}
          />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-[11px] text-muted">
              Server
              <input
                value={server}
                onChange={(e) => setServer(e.target.value)}
                placeholder="* (any)"
                aria-label="Server pattern"
                className={`${inputCls} font-mono mt-0.5`}
              />
            </label>
            <label className="text-[11px] text-muted">
              Tool
              <input
                value={tool}
                onChange={(e) => setTool(e.target.value)}
                placeholder="* (any)"
                aria-label="Tool pattern"
                className={`${inputCls} font-mono mt-0.5`}
              />
            </label>
          </div>
          <p className="text-[11px] text-muted">
            Patterns accept <code>*</code> and <code>?</code>. Built-in Precursor tools use the
            server <code>precursor</code>.
          </p>
          <label className="flex items-center gap-2 text-xs cursor-pointer select-none">
            <input type="checkbox" checked={withArg} onChange={(e) => setWithArg(e.target.checked)} />
            Only when an argument matches
          </label>
          {withArg && (
            <div className="grid grid-cols-[6rem_1fr] gap-2">
              <input
                value={arg}
                onChange={(e) => setArg(e.target.value)}
                aria-label="Argument name"
                placeholder="path"
                className={`${inputCls} font-mono`}
              />
              <Select
                fullWidth
                size="sm"
                ariaLabel="Argument condition"
                value={op}
                onChange={(v) => setOp(v as ArgClause["op"])}
                options={OPS}
              />
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                aria-label="Argument value"
                placeholder={op === "under" ? "notes/drafts" : "value"}
                className={`${inputCls} font-mono col-span-2`}
              />
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={saving || (withArg && !value.trim())}
              onClick={() => void add()}
              className="px-2.5 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
            >
              {saving ? "Adding…" : "Add rule"}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              className="px-2.5 py-1 text-xs text-muted hover:text-text"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="flex items-center gap-1 text-xs text-accent hover:opacity-80"
        >
          <Plus size={12} /> Add a rule
        </button>
      )}
      {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
    </div>
  );
}
