import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Database,
  FileCode2,
  FolderOpen,
  Loader2,
  RotateCcw,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { notifyDefinitionsChanged } from "../lib/definitionsEvents";
import { openWorkspaceFile } from "../lib/workspaceLink";
import type {
  DefinitionsCheckReport,
  MigrationAction,
  MigrationItem,
  MigrationItemDetail,
  MigrationPreview,
  MigrationResult,
} from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

/**
 * Settings → Definition files: the guided move from database-declared to
 * file-declared agents and workflows (docs/definitions.md), ending — on
 * request — with the irreversible database cleanup.
 */

type StepId = "overview" | "review" | "migrate" | "try" | "cleanup" | "done";

const STEPS: { id: StepId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "review", label: "Review" },
  { id: "migrate", label: "Migrate" },
  { id: "try", label: "Try it out" },
  { id: "cleanup", label: "Clean up" },
  { id: "done", label: "Done" },
];

const ACTION_LABEL: Record<MigrationAction, string> = {
  create: "New file",
  regenerate: "Rewritten",
  unchanged: "Up to date",
  new_from_disk: "From the folder",
};

const ACTION_STYLE: Record<MigrationAction, string> = {
  create: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  regenerate: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  unchanged: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  new_from_disk: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
};

const CONFIRM_PHRASE = "clean up";

function plural(n: number, word: string, many?: string): string {
  return `${n} ${n === 1 ? word : (many ?? `${word}s`)}`;
}

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Where the wizard opens for a given stage, and how far one may click. */
function stageBounds(stage: MigrationPreview["stage"]): { start: number; reachable: number } {
  if (stage === "finalized") return { start: 5, reachable: 5 };
  if (stage === "files") return { start: 3, reachable: 4 };
  return { start: 0, reachable: 2 };
}

export function DefinitionsMigrationWizard() {
  const confirmAction = useConfirm();
  const [preview, setPreview] = useState<MigrationPreview | null>(null);
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (jump = true) => {
    setLoading(true);
    setError(null);
    try {
      const next = await api.definitions.migration();
      setPreview(next);
      if (jump) setStep(stageBounds(next.stage).start);
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!preview) {
    return (
      <div className="space-y-2">
        <Intro />
        {loading && (
          <p className="flex items-center gap-1.5 text-[12px] text-muted">
            <Loader2 size={12} className="animate-spin" /> Checking your agents and workflows…
          </p>
        )}
        {error && <p className="text-[12px] text-red-500">{error}</p>}
      </div>
    );
  }

  const { start, reachable } = stageBounds(preview.stage);
  const current = STEPS[step].id;

  return (
    <div className="space-y-4">
      <Intro />
      <Stepper step={step} start={start} reachable={reachable} onSelect={setStep} />
      <div className="rounded border border-border p-4">
        {current === "overview" && (
          <OverviewStep preview={preview} onNext={() => setStep(1)} readOnly={preview.stage !== "database"} />
        )}
        {current === "review" && (
          <ReviewStep
            preview={preview}
            onBack={() => setStep(0)}
            onNext={() => setStep(2)}
          />
        )}
        {current === "migrate" && (
          <MigrateStep
            preview={preview}
            onBack={() => setStep(1)}
            onMigrated={() => void load()}
          />
        )}
        {current === "try" && (
          <TryStep
            preview={preview}
            onReverted={() => void load()}
            onNext={() => setStep(4)}
            confirmAction={confirmAction}
          />
        )}
        {current === "cleanup" && (
          <CleanupStep
            preview={preview}
            onBack={() => setStep(3)}
            onFinalized={() => void load()}
            onRecheck={() => void load(false)}
          />
        )}
        {current === "done" && <DoneStep preview={preview} />}
      </div>
      {error && <p className="text-[12px] text-red-500">{error}</p>}
    </div>
  );
}

function Intro() {
  return (
    <div className="flex items-start gap-2">
      <FileCode2 size={16} className="mt-0.5 text-accent" aria-hidden />
      <div className="space-y-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          Migrate to definition files
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
            Work in progress
          </span>
        </p>
        <p className="text-[12px] text-muted">
          Declare your agents and workflows in YAML files you can edit, review and keep in
          git. The database then keeps only what happens when they run.
        </p>
      </div>
    </div>
  );
}

function Stepper({
  step,
  start,
  reachable,
  onSelect,
}: {
  step: number;
  start: number;
  reachable: number;
  onSelect: (i: number) => void;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[11px]" aria-label="Migration steps">
      {STEPS.map((s, i) => {
        const done = i < start;
        const active = i === step;
        const enabled = i <= reachable;
        return (
          <li key={s.id} className="flex items-center gap-1">
            <button
              type="button"
              disabled={!enabled}
              onClick={() => onSelect(i)}
              aria-current={active ? "step" : undefined}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 transition disabled:cursor-default ${
                active
                  ? "border-accent bg-accent/10 font-medium text-accent"
                  : done
                    ? "border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300"
                    : enabled
                      ? "border-border text-text/80 hover:bg-surface"
                      : "border-border text-muted opacity-60"
              }`}
            >
              {done ? (
                <Check size={11} aria-hidden />
              ) : (
                <span className="tabular-nums">{i + 1}</span>
              )}
              {s.label}
            </button>
            {i < STEPS.length - 1 && <ChevronRight size={11} className="text-muted" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}

function StepTitle({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="mb-3 space-y-0.5">
      <h3 className="text-sm font-medium">{children}</h3>
      {hint && <p className="text-[12px] text-muted">{hint}</p>}
    </div>
  );
}

function Nav({
  onBack,
  children,
}: {
  onBack?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="mt-4 flex items-center gap-2 border-t border-border pt-3">
      {onBack && (
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1 text-[12px] hover:bg-surface"
        >
          <ArrowLeft size={12} aria-hidden /> Back
        </button>
      )}
      <div className="flex-1" />
      {children}
    </div>
  );
}

function PrimaryButton({
  onClick,
  disabled,
  busy,
  danger,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className={`inline-flex items-center gap-1.5 rounded px-3 py-1 text-[12px] font-medium text-white disabled:opacity-50 ${
        danger ? "bg-red-600 hover:bg-red-700" : "bg-accent"
      }`}
    >
      {busy && <Loader2 size={12} className="animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

function OpenFiles({ preview, label = "Open in Files" }: { preview: MigrationPreview; label?: string }) {
  if (!preview.workspace || !preview.sample_path) return null;
  const ws = preview.workspace;
  const path = preview.sample_path;
  return (
    <button
      type="button"
      onClick={() => openWorkspaceFile(ws.slug, path)}
      className="inline-flex items-center gap-1 text-[12px] text-accent hover:underline"
    >
      <FolderOpen size={12} aria-hidden /> {label}
    </button>
  );
}

function IssueList({ issues }: { issues: MigrationPreview["issues"] }) {
  if (issues.length === 0) return null;
  return (
    <ul className="max-h-40 space-y-0.5 overflow-auto rounded bg-surface/50 p-2 text-[11px]">
      {issues.map((issue, i) => (
        <li key={i}>
          <span className={issue.severity === "error" ? "text-red-500" : "text-amber-500"}>
            {issue.severity}
          </span>{" "}
          {issue.path && <code className="text-muted">{issue.path}</code>}{" "}
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

function Blockers({ blockers, title }: { blockers: string[]; title: string }) {
  if (blockers.length === 0) return null;
  return (
    <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-200">
      <p className="font-medium">{title}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        {blockers.map((b, i) => (
          <li key={i}>{b}</li>
        ))}
      </ul>
    </div>
  );
}

// --- 1. Overview -------------------------------------------------------------

function OverviewStep({
  preview,
  onNext,
  readOnly,
}: {
  preview: MigrationPreview;
  onNext: () => void;
  readOnly: boolean;
}) {
  const agents = preview.items.filter((i) => i.kind === "agent" && i.id !== null).length;
  const workflows = preview.items.filter((i) => i.kind === "workflow" && i.id !== null).length;
  const stepsOutline: [string, string][] = [
    ["Review", "See, for each agent and workflow, the file that will be written — and what's on disk now."],
    ["Migrate", "A copy of the database is taken, the files are written and checked one by one against the database. Nothing switches if any differs."],
    ["Try it out", "Agents and workflows run from their files. Edit them in Files; switch back to the database at any time."],
    ["Clean up", "When you're sure, clear the declarations out of the database. Switching back isn't possible after that."],
  ];
  return (
    <div className="space-y-3 text-[12px]">
      <StepTitle hint="What changes, where the files go, and what's needed first.">
        Overview
      </StepTitle>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded bg-surface/50 p-3">
          <p className="mb-1 flex items-center gap-1.5 font-medium">
            <Database size={12} aria-hidden /> Today
          </p>
          <p className="text-muted">
            {readOnly ? (
              "Declared by definition files."
            ) : (
              <>
                {plural(agents, "agent")} and {plural(workflows, "workflow")} declared by the
                database.
              </>
            )}
          </p>
        </div>
        <div className="rounded bg-surface/50 p-3">
          <p className="mb-1 flex items-center gap-1.5 font-medium">
            <FileCode2 size={12} aria-hidden /> Where the files go
          </p>
          <p className="text-muted">
            {preview.workspace ? (
              <>
                The <strong className="text-text/80">{preview.workspace.name}</strong> workspace, in
                the Files section
              </>
            ) : (
              "The definitions folder"
            )}
            : <code className="break-all">{preview.folder}</code>
          </p>
        </div>
      </div>
      <div>
        <p className="mb-1 font-medium">What stays in the database</p>
        <p className="text-muted">
          Run history, events and artifacts, schedules, triggers and webhook tokens, workflow
          state, and read markers — everything that changes while agents run.
        </p>
      </div>
      <div>
        <p className="mb-1 font-medium">The steps</p>
        <ol className="space-y-1">
          {stepsOutline.map(([label, text], i) => (
            <li key={label} className="flex gap-2">
              <span className="w-4 shrink-0 text-right tabular-nums text-muted">{i + 2}.</span>
              <span>
                <strong className="text-text/80">{label}.</strong>{" "}
                <span className="text-muted">{text}</span>
              </span>
            </li>
          ))}
        </ol>
      </div>
      {!readOnly && (
        <Blockers blockers={preview.blockers} title="To fix before migrating" />
      )}
      {!readOnly && preview.blockers.length === 0 && (
        <p className="flex items-center gap-1.5 text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={13} aria-hidden /> Ready: nothing is mid-run and Agents mode is on.
        </p>
      )}
      <Nav>
        {!readOnly && (
          <PrimaryButton onClick={onNext}>
            Review the files <ArrowRight size={12} aria-hidden />
          </PrimaryButton>
        )}
      </Nav>
    </div>
  );
}

// --- 2. Review ---------------------------------------------------------------

function ReviewStep({
  preview,
  onBack,
  onNext,
}: {
  preview: MigrationPreview;
  onBack: () => void;
  onNext: () => void;
}) {
  const readOnly = preview.stage !== "database";
  const [open, setOpen] = useState<string | null>(null);
  const counts = useMemo(() => {
    const c: Record<MigrationAction, number> = { create: 0, regenerate: 0, unchanged: 0, new_from_disk: 0 };
    for (const i of preview.items) c[i.action] += 1;
    return c;
  }, [preview.items]);
  const [filter, setFilter] = useState<MigrationAction | "all">("all");
  const items = preview.items.filter((i) => filter === "all" || i.action === filter);

  if (readOnly) {
    return (
      <div className="text-[12px]">
        <StepTitle>Review</StepTitle>
        <p className="text-muted">
          Reviewed and migrated{preview.migrated ? ` on ${when(preview.migrated.at)}` : ""}. The
          files are now the source; see them in Files.
        </p>
        <div className="mt-2">
          <OpenFiles preview={preview} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 text-[12px]">
      <StepTitle hint="Each line is a file. Open one to see exactly what will be written, next to what's on disk now.">
        Review the files
      </StepTitle>
      <div className="flex flex-wrap gap-1.5">
        <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>
          All {preview.items.length}
        </FilterChip>
        {(Object.keys(counts) as MigrationAction[])
          .filter((a) => counts[a] > 0)
          .map((a) => (
            <FilterChip key={a} active={filter === a} onClick={() => setFilter(a)} className={ACTION_STYLE[a]}>
              {ACTION_LABEL[a]} {counts[a]}
            </FilterChip>
          ))}
      </div>
      {preview.items.length === 0 ? (
        <p className="text-muted">No agents or workflows yet — nothing to migrate.</p>
      ) : (
        <ul className="divide-y divide-border rounded border border-border">
          {items.map((item) => {
            const key = `${item.kind}:${item.id ?? item.path}`;
            return (
              <ReviewRow
                key={key}
                item={item}
                open={open === key}
                onToggle={() => setOpen(open === key ? null : key)}
              />
            );
          })}
        </ul>
      )}
      <IssueList issues={preview.issues} />
      <Blockers blockers={preview.blockers} title="To fix before migrating" />
      <Nav onBack={onBack}>
        <PrimaryButton onClick={onNext} disabled={!preview.ready}>
          Continue <ArrowRight size={12} aria-hidden />
        </PrimaryButton>
      </Nav>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  className = "",
  children,
}: {
  active: boolean;
  onClick: () => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full px-2 py-0.5 text-[11px] ${className} ${
        active ? "ring-1 ring-accent" : "opacity-80 hover:opacity-100"
      } ${className ? "" : "bg-surface"}`}
    >
      {children}
    </button>
  );
}

function ReviewRow({
  item,
  open,
  onToggle,
}: {
  item: MigrationItem;
  open: boolean;
  onToggle: () => void;
}) {
  const [detail, setDetail] = useState<MigrationItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canOpen = item.id !== null && item.action !== "new_from_disk";

  useEffect(() => {
    if (!open || !canOpen || detail || item.id === null) return;
    api.definitions
      .migrationItem(item.kind, item.id)
      .then(setDetail)
      .catch((e) => setError(apiErrorMessage(e)));
  }, [open, canOpen, detail, item.kind, item.id]);

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-surface/60"
      >
        {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${ACTION_STYLE[item.action]}`}>
          {ACTION_LABEL[item.action]}
        </span>
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">{item.kind}</span>
        <span className="min-w-0 flex-1 truncate">{item.name}</span>
        <code className="hidden min-w-0 max-w-[45%] truncate text-[10px] text-muted sm:block">
          {item.path ?? "new file"}
        </code>
      </button>
      {open && (
        <div className="space-y-2 px-2 pb-3 pt-1">
          {item.reason && <p className="text-[11px] text-muted">{item.reason}</p>}
          {!canOpen ? (
            <p className="text-[11px] text-muted">
              Already in the folder, but no agent or workflow here carries its id. It becomes one
              after the switch, and what it's allowed to do waits for your review.
            </p>
          ) : error ? (
            <p className="text-[11px] text-red-500">{error}</p>
          ) : !detail ? (
            <p className="flex items-center gap-1.5 text-[11px] text-muted">
              <Loader2 size={11} className="animate-spin" /> Loading…
            </p>
          ) : (
            <div className={`grid gap-2 ${detail.current !== null && detail.action !== "unchanged" ? "lg:grid-cols-2" : ""}`}>
              <YamlBlock
                title={
                  detail.action === "unchanged"
                    ? `${detail.path} — kept exactly as it is`
                    : detail.action === "create"
                      ? "Will be written"
                      : "Will be written (from the database)"
                }
                text={detail.action === "unchanged" && detail.current ? detail.current : detail.proposed}
              />
              {detail.current !== null && detail.action !== "unchanged" && (
                <YamlBlock title={`On disk now — ${detail.path}`} text={detail.current} muted />
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function YamlBlock({ title, text, muted }: { title: string; text: string; muted?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 truncate text-[10px] font-medium text-muted">{title}</p>
      <pre
        className={`max-h-72 overflow-auto rounded border border-border p-2 font-mono text-[11px] leading-relaxed ${
          muted ? "bg-surface/30 text-text/70" : "bg-surface/60"
        }`}
      >
        {text}
      </pre>
    </div>
  );
}

// --- 3. Migrate --------------------------------------------------------------

function MigrateStep({
  preview,
  onBack,
  onMigrated,
}: {
  preview: MigrationPreview;
  onBack: () => void;
  onMigrated: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MigrationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (preview.stage !== "database") {
    const m = preview.migrated;
    return (
      <div className="text-[12px]">
        <StepTitle>Migrate</StepTitle>
        {m ? (
          <ul className="space-y-0.5 text-muted">
            <li>Migrated on {when(m.at)}.</li>
            <li>
              {plural(m.created, "file")} written, {m.regenerated} rewritten, {m.unchanged} kept as
              they were
              {m.added_from_disk ? `, ${m.added_from_disk} added from the folder` : ""}.
            </li>
            {m.snapshot && (
              <li>
                Database copy taken first: <code className="break-all">{m.snapshot}</code>
              </li>
            )}
          </ul>
        ) : (
          <p className="text-muted">Declared by files.</p>
        )}
      </div>
    );
  }

  const create = preview.items.filter((i) => i.action === "create").length;
  const rewrite = preview.items.filter((i) => i.action === "regenerate").length;
  const keep = preview.items.filter((i) => i.action === "unchanged").length;
  const errors = preview.issues.filter((i) => i.severity === "error").length;

  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const r = await api.definitions.migrate(true);
      setResult(r);
      if (r.ok) {
        notifyDefinitionsChanged();
        onMigrated();
      }
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 text-[12px]">
      <StepTitle hint="Four things happen, in this order. If the check in 3 finds any difference, nothing is switched.">
        Migrate
      </StepTitle>
      <ol className="space-y-1.5">
        <li className="flex gap-2">
          <span className="w-4 shrink-0 text-right text-muted">1.</span>
          <span>
            <strong className="text-text/80">Back up.</strong>{" "}
            <span className="text-muted">A copy of the database goes to the data folder, under definitions-migration.</span>
          </span>
        </li>
        <li className="flex gap-2">
          <span className="w-4 shrink-0 text-right text-muted">2.</span>
          <span>
            <strong className="text-text/80">Write.</strong>{" "}
            <span className="text-muted">
              {plural(create, "new file")}
              {rewrite ? `, ${rewrite} rewritten from the database` : ""}
              {keep ? `, ${keep} left exactly as they are` : ""}.
            </span>
          </span>
        </li>
        <li className="flex gap-2">
          <span className="w-4 shrink-0 text-right text-muted">3.</span>
          <span>
            <strong className="text-text/80">Check.</strong>{" "}
            <span className="text-muted">Every agent and workflow must have one valid file saying exactly what the database says.</span>
          </span>
        </li>
        <li className="flex gap-2">
          <span className="w-4 shrink-0 text-right text-muted">4.</span>
          <span>
            <strong className="text-text/80">Switch.</strong>{" "}
            <span className="text-muted">What the files allow is recorded as accepted (it's what you had), and they become the source.</span>
          </span>
        </li>
      </ol>
      {preview.needs_confirmation && (
        <label className="flex items-start gap-2 rounded bg-amber-500/10 px-3 py-2">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            {rewrite > 0 && (
              <>
                I've reviewed the {plural(rewrite, "file")} that will be rewritten from the database
                — edits made in them since the export will be replaced.{" "}
              </>
            )}
            {errors > 0 && (
              <>
                {plural(errors, "definition")} with errors will be written as-is and refuse to run
                until fixed.
              </>
            )}
          </span>
        </label>
      )}
      {result && !result.ok && (
        <Blockers
          blockers={result.mismatches}
          title="The files didn't match the database, so nothing was switched"
        />
      )}
      {error && <p className="text-red-500">{error}</p>}
      <Nav onBack={onBack}>
        <PrimaryButton
          onClick={() => void run()}
          busy={busy}
          disabled={!preview.ready || (preview.needs_confirmation && !acknowledged)}
        >
          Back up and migrate
        </PrimaryButton>
      </Nav>
    </div>
  );
}

// --- 4. Try it out -----------------------------------------------------------

function TryStep({
  preview,
  onReverted,
  onNext,
  confirmAction,
}: {
  preview: MigrationPreview;
  onReverted: () => void;
  onNext: () => void;
  confirmAction: ReturnType<typeof useConfirm>;
}) {
  const [check, setCheck] = useState<DefinitionsCheckReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.definitions.check().then(setCheck).catch(() => {});
  }, []);

  if (preview.stage === "finalized") {
    return (
      <div className="text-[12px]">
        <StepTitle>Try it out</StepTitle>
        <p className="text-muted">Done — the files have been the only source since the cleanup.</p>
      </div>
    );
  }

  async function revert(): Promise<void> {
    const ok = await confirmAction({
      title: "Switch back to the database?",
      message:
        "What the files say now is copied into the database, which then declares your agents and workflows again. The files stay where they are, and you can migrate again later.",
      confirmLabel: "Switch back",
      variant: "warning",
    });
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await api.definitions.revert();
      notifyDefinitionsChanged();
      onReverted();
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const errors = check?.issues.filter((i) => i.severity === "error") ?? [];
  const warnings = check?.issues.filter((i) => i.severity === "warning") ?? [];
  const reviews = preview.cleanup?.warnings ?? [];

  return (
    <div className="space-y-3 text-[12px]">
      <div className="flex items-start gap-2 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-emerald-800 dark:text-emerald-200">
        <CheckCircle2 size={14} className="mt-0.5 shrink-0" aria-hidden />
        <div>
          <p className="font-medium">Your agents and workflows are declared by their files.</p>
          {preview.migrated && (
            <p className="text-[11px] opacity-80">
              Since {when(preview.migrated.at)}
              {preview.migrated.snapshot ? ` · database copy: ${preview.migrated.snapshot}` : ""}
            </p>
          )}
        </div>
      </div>
      <StepTitle hint="Use it for a while. Until you clean up, the database still holds its copy and you can switch back.">
        Try it out
      </StepTitle>
      <ul className="space-y-1 text-muted">
        <li>• Open a file in the Files section and edit it: the change shows in the app within a second, and the check runs on save.</li>
        <li>• Edit an agent or workflow in the app: its file is rewritten.</li>
        <li>• Run a workflow: its run records which version of the file it used.</li>
      </ul>
      <OpenFiles preview={preview} label="Open the files" />

      <div className="rounded bg-surface/50 p-3">
        <p className="mb-1 font-medium">Folder check</p>
        {!check ? (
          <p className="flex items-center gap-1.5 text-muted">
            <Loader2 size={11} className="animate-spin" /> Checking…
          </p>
        ) : (
          <>
            <p className={errors.length ? "text-red-600 dark:text-red-400" : "text-emerald-700 dark:text-emerald-300"}>
              {plural(check.files.length, "file")} · {plural(errors.length, "error")} ·{" "}
              {plural(warnings.length, "warning")}
            </p>
            <IssueList issues={check.issues} />
          </>
        )}
      </div>
      {reviews.length > 0 && (
        <div className="rounded bg-amber-500/10 px-3 py-2 text-amber-800 dark:text-amber-200">
          <p className="flex items-center gap-1.5 font-medium">
            <ShieldAlert size={12} aria-hidden /> Waiting for your review
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px]">
            {reviews.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="text-red-500">{error}</p>}
      <Nav>
        <button
          type="button"
          disabled={busy || preview.forced}
          onClick={() => void revert()}
          data-tooltip={preview.forced ? "Unset PRECURSOR_DEFINITIONS_SOURCE to switch back" : undefined}
          className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} aria-hidden />}
          Switch back to the database
        </button>
        <PrimaryButton onClick={onNext}>
          I'm ready to clean up <ArrowRight size={12} aria-hidden />
        </PrimaryButton>
      </Nav>
    </div>
  );
}

// --- 5. Clean up -------------------------------------------------------------

function CleanupStep({
  preview,
  onBack,
  onFinalized,
  onRecheck,
}: {
  preview: MigrationPreview;
  onBack: () => void;
  onFinalized: () => void;
  onRecheck: () => void;
}) {
  const [understood, setUnderstood] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const c = preview.cleanup;

  if (preview.stage === "finalized") {
    return (
      <div className="text-[12px]">
        <StepTitle>Clean up</StepTitle>
        <p className="text-muted">Cleaned up on {when(preview.finalized?.at)}.</p>
      </div>
    );
  }
  if (!c) return null;

  const confirmed = understood && phrase.trim().toLowerCase() === CONFIRM_PHRASE;

  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    setIssues([]);
    try {
      const r = await api.definitions.finalize();
      if (r.ok) {
        notifyDefinitionsChanged();
        onFinalized();
      }
      else setIssues(r.issues.map((i) => i.message));
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 text-[12px]">
      <StepTitle hint="The last step: the database stops holding a copy of your agents and workflows.">
        Clean up the database
      </StepTitle>
      <div className="flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-800 dark:text-red-200">
        <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
        <p>
          <strong>This can't be undone from the app.</strong> After it, switching back to the
          database is no longer possible: the only way back is restoring the copy of the database
          taken just before.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded bg-surface/50 p-3">
          <p className="mb-1 flex items-center gap-1.5 font-medium">
            <Trash2 size={12} aria-hidden /> Cleared from the database
          </p>
          <ul className="space-y-0.5 text-muted">
            <li>The prompts and settings of {plural(c.agents, "agent")}</li>
            <li>The prompts of {plural(c.step_prompts, "step")} that have their own</li>
            <li>The settings of {plural(c.workflows, "workflow")}</li>
            <li>The declarations of {plural(c.steps, "workflow step")}</li>
          </ul>
        </div>
        <div className="rounded bg-surface/50 p-3">
          <p className="mb-1 flex items-center gap-1.5 font-medium">
            <Database size={12} aria-hidden /> Kept
          </p>
          <ul className="space-y-0.5 text-muted">
            <li>Run history, events, artifacts and state</li>
            <li>Schedules, triggers and webhook tokens</li>
            <li>Names, as a search index kept in step with the files</li>
            <li>Each row's link to its file, and what you've accepted</li>
          </ul>
        </div>
      </div>
      {c.missing_files.length > 0 && (
        <p className="rounded bg-sky-500/10 px-3 py-2 text-sky-800 dark:text-sky-200">
          {plural(c.missing_files.length, "agent or workflow", "agents or workflows")} still have
          no file ({c.missing_files.join(", ")}). They're written from the database first.
        </p>
      )}
      {c.warnings.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-amber-700 dark:text-amber-300">
          {c.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      <Blockers blockers={c.blockers} title="Fix these first — the files will be all there is" />
      {issues.length > 0 && (
        <Blockers blockers={issues} title="The check failed, so nothing was cleared" />
      )}
      {c.ready && (
        <div className="space-y-2 rounded border border-border p-3">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={understood}
              onChange={(e) => setUnderstood(e.target.checked)}
              className="mt-0.5"
            />
            <span>I understand switching back to the database won't be possible any more.</span>
          </label>
          <label className="block space-y-1">
            <span className="text-muted">
              Type <code className="rounded bg-surface px-1">{CONFIRM_PHRASE}</code> to confirm
            </span>
            <input
              type="text"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              aria-label={`Type ${CONFIRM_PHRASE} to confirm`}
              className="w-48 rounded border border-border bg-surface px-2 py-1 text-[12px]"
            />
          </label>
        </div>
      )}
      {error && <p className="text-red-500">{error}</p>}
      <Nav onBack={onBack}>
        {!c.ready && (
          <button
            type="button"
            onClick={onRecheck}
            className="rounded border border-border px-2.5 py-1 text-[12px] hover:bg-surface"
          >
            Check again
          </button>
        )}
        <PrimaryButton danger onClick={() => void run()} busy={busy} disabled={!c.ready || !confirmed}>
          Clean up the database
        </PrimaryButton>
      </Nav>
    </div>
  );
}

// --- 6. Done -----------------------------------------------------------------

function DoneStep({ preview }: { preview: MigrationPreview }) {
  const f = preview.finalized;
  return (
    <div className="space-y-3 text-[12px]">
      <div className="flex items-center gap-3">
        <CheckCircle2 size={28} className="shrink-0 text-emerald-500" aria-hidden />
        <div>
          <h3 className="text-sm font-semibold">Migration complete</h3>
          <p className="text-muted">
            Your agents and workflows are declared only by their files
            {f ? ` since ${when(f.at)}` : ""}. The database keeps what happens when they run.
          </p>
        </div>
      </div>
      {f && (
        <ul className="space-y-0.5 rounded bg-surface/50 p-3 text-muted">
          <li>
            Cleared: {plural(f.agents, "agent")}, {plural(f.step_prompts, "step prompt")},{" "}
            {plural(f.workflows, "workflow")}, {plural(f.steps, "workflow step")}.
          </li>
          {f.snapshot && (
            <li>
              The database as it was just before:{" "}
              <code className="break-all">{f.snapshot}</code> — keep it until you're confident,
              then delete it.
            </li>
          )}
          {preview.migrated?.snapshot && (
            <li>
              And before the migration: <code className="break-all">{preview.migrated.snapshot}</code>
            </li>
          )}
        </ul>
      )}
      <div>
        <p className="mb-1 font-medium">From now on</p>
        <ul className="space-y-0.5 text-muted">
          <li>• Edit agents and workflows in the app or as files — both write the files.</li>
          <li>• A file that widens what an agent may do waits for your review before it runs.</li>
          <li>• A file that goes missing stops its agent or workflow: restore it from git or the copy above.</li>
          <li>• To keep them in git, point PRECURSOR_DEFINITIONS_WORKSPACE at a cloned repository.</li>
        </ul>
      </div>
      <OpenFiles preview={preview} label="Open your definition files" />
    </div>
  );
}
