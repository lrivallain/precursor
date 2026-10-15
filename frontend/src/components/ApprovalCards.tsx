import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, ShieldAlert, X } from "lucide-react";
import { apiErrorMessage } from "../lib/api";
import type { ArgClause, ToolApproval, ToolApprovalDecision } from "../lib/types";

interface Props {
  approvals: ToolApproval[];
  onDecide: (approval: ToolApproval, decision: ToolApprovalDecision) => Promise<void>;
}

function prettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

const PATH_ARGS = ["path", "file_path", "filepath", "file", "filename", "dir", "directory", "folder"];

/** The first path-like string argument of a call, to offer a folder condition on. */
function pathArgument(raw: string): { arg: string; value: string } | null {
  try {
    const parsed: unknown = JSON.parse(raw || "{}");
    if (parsed === null || typeof parsed !== "object") return null;
    const args = parsed as Record<string, unknown>;
    for (const name of PATH_ARGS) {
      const v = args[name];
      if (typeof v === "string" && v.trim()) return { arg: name, value: v.trim() };
    }
  } catch {
    // Unparseable arguments simply offer no path condition.
  }
  return null;
}

/** The condition a rule would carry: inside the call's folder, or that exact path. */
function defaultClause(p: { arg: string; value: string }): ArgClause {
  const cut = p.value.replace(/\/+$/, "").lastIndexOf("/");
  if (cut > 0) return { arg: p.arg, op: "under", value: p.value.slice(0, cut) };
  return { arg: p.arg, op: "equals", value: p.value };
}

/** The inline cards a parked turn waits on, rendered at the transcript tail. */
export function ApprovalCards({ approvals, onDecide }: Props) {
  if (approvals.length === 0) return null;
  const waiting = approvals.filter((a) => a.status === "pending").length;
  return (
    <div className="flex flex-col gap-2" role="group" aria-label="Approvals needed">
      {approvals.map((a) => (
        <ApprovalCard key={a.id} approval={a} onDecide={onDecide} />
      ))}
      {waiting > 0 && (
        <p className="px-1 text-[11px] text-muted">
          The reply continues once {waiting === 1 ? "this is" : "these are"} answered. Sending a
          new message cancels {waiting === 1 ? "it" : "them"}.
        </p>
      )}
    </div>
  );
}

function ApprovalCard({
  approval: a,
  onDecide,
}: {
  approval: ToolApproval;
  onDecide: Props["onDecide"];
}) {
  const [open, setOpen] = useState(false);
  const [declining, setDeclining] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [always, setAlways] = useState(false);
  const [everywhere, setEverywhere] = useState(false);
  const [limit, setLimit] = useState(true);
  const [limitValue, setLimitValue] = useState("");
  const isIntent = a.kind === "intent";
  const settled = a.status !== "pending";
  const pathArg = isIntent ? null : pathArgument(a.arguments);
  const clause = pathArg ? defaultClause(pathArg) : null;

  async function decide(decision: ToolApprovalDecision): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await onDecide(a, decision);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not record the answer"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full rounded-lg border border-amber-500/50 bg-amber-500/5 text-sm">
      <div className="flex items-center gap-2 px-3 py-2">
        <ShieldAlert size={14} className="shrink-0 text-amber-600 dark:text-amber-400" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
          {isIntent ? "Model asks" : "Approval needed"}
          {!isIntent && a.server && ` · ${a.server}`}
        </span>
        {!isIntent && <span className="truncate font-mono text-xs">{a.raw_tool || a.tool_name}</span>}
        {settled && (
          <span className="ml-auto flex items-center gap-1 text-[11px] text-muted">
            <Loader2 size={11} className="animate-spin" />
            {a.status === "approved" ? "Approved" : "Denied"} · resuming…
          </span>
        )}
      </div>
      {a.reason && <p className="px-3 pb-2 text-xs text-text/90 whitespace-pre-wrap">{a.reason}</p>}
      {!isIntent && (
        <div className="px-3 pb-2">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-text"
            aria-expanded={open}
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Arguments
          </button>
          {open && (
            <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded border border-amber-500/20 bg-bg/60 p-2 text-xs">
              {prettyJson(a.arguments || "{}")}
            </pre>
          )}
        </div>
      )}
      {!settled && (
        <div className="flex flex-col gap-2 border-t border-amber-500/30 px-3 py-2">
          {declining && (
            <input
              autoFocus
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !busy) {
                  void decide({ approve: false, feedback: feedback.trim() || null });
                }
              }}
              placeholder="Tell the model why or what to do instead (optional)"
              aria-label="Reason for declining"
              className="w-full rounded border border-border bg-bg px-2 py-1 text-xs"
            />
          )}
          <div className="flex flex-wrap items-center gap-2">
            {declining ? (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void decide({ approve: false, feedback: feedback.trim() || null })}
                  className="inline-flex items-center gap-1 rounded bg-red-500 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
                >
                  <X size={12} /> Deny
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setDeclining(false)}
                  className="text-xs text-muted hover:text-text"
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void decide({ approve: true, scope: "once" })}
                  className="inline-flex items-center gap-1 rounded bg-amber-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                >
                  <Check size={12} /> {isIntent ? "Go ahead" : "Approve"}
                </button>
                {!isIntent && (
                  <button
                    type="button"
                    disabled={busy}
                    aria-expanded={always}
                    onClick={() => {
                      setAlways((o) => !o);
                      setLimitValue(clause?.value ?? "");
                    }}
                    className="rounded border border-amber-500/50 px-2.5 py-1 text-xs hover:bg-amber-500/10 disabled:opacity-50"
                    data-tooltip="Approve this and save a rule so the model can call this tool again without asking"
                  >
                    Always allow…
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setDeclining(true)}
                  className="rounded border border-border px-2.5 py-1 text-xs hover:bg-border/40 disabled:opacity-50"
                >
                  {isIntent ? "Not yet" : "Deny…"}
                </button>
              </>
            )}
            {busy && <Loader2 size={12} className="animate-spin text-muted" />}
          </div>
          {always && !declining && !isIntent && (
            <div className="flex flex-col gap-1.5 rounded border border-amber-500/30 bg-bg/60 p-2 text-xs">
              <span className="text-muted">
                Save a rule allowing <code className="font-mono">{a.raw_tool || a.tool_name}</code>
                {a.server && (
                  <>
                    {" "}on <code className="font-mono">{a.server}</code>
                  </>
                )}
                :
              </span>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name={`scope-${a.id}`}
                  checked={!everywhere}
                  onChange={() => setEverywhere(false)}
                />
                In this conversation
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name={`scope-${a.id}`}
                  checked={everywhere}
                  onChange={() => setEverywhere(true)}
                />
                In every conversation
              </label>
              {clause && (
                <div className="flex flex-col gap-1">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={limit} onChange={(e) => setLimit(e.target.checked)} />
                    Only when <code className="font-mono">{clause.arg}</code>{" "}
                    {clause.op === "under" ? "is inside" : "is exactly"}
                  </label>
                  {limit && (
                    <input
                      value={limitValue}
                      onChange={(e) => setLimitValue(e.target.value)}
                      aria-label="Path condition"
                      className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
                    />
                  )}
                </div>
              )}
              <div>
                <button
                  type="button"
                  disabled={busy || (!!clause && limit && !limitValue.trim())}
                  onClick={() =>
                    void decide({
                      approve: true,
                      scope: everywhere ? "global" : "conversation",
                      arg_match:
                        clause && limit ? [{ ...clause, value: limitValue.trim() }] : [],
                    })
                  }
                  className="inline-flex items-center gap-1 rounded bg-amber-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                >
                  <Check size={12} /> Approve and save rule
                </button>
              </div>
            </div>
          )}
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
      )}
    </div>
  );
}
