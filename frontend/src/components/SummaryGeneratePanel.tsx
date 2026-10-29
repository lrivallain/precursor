import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  CopyPlus,
  FileCode2,
  Loader2,
  Mic,
  Pencil,
  ScrollText,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { api } from "../lib/api";
import type { SummaryTemplatesState } from "../lib/useSummaryTemplates";
import { openWorkspaceFile } from "../lib/workspaceLink";
import { Select } from "./Select";

export type SummarySource = "recording" | "transcript";

interface Props {
  templates: SummaryTemplatesState;
  /** The session's language tag, named in the "Meeting language" option. */
  sessionLanguage: string | null;
  /** A recap is already there: generating replaces it. */
  hasSummary: boolean;
  /** Something was recorded locally. */
  canUseRecording: boolean;
  /** Why the Teams transcript can't be used, or null when it can. */
  transcriptUnavailable: string | null;
  /** A generation (from either source) is running. */
  busy: "recording" | "transcript" | null;
  onGenerate: (source: SummarySource) => void;
  /** Surface a message in the Summary tab (e.g. where a template file was saved). */
  onNotice: (message: string, tone: "info" | "error") => void;
}

/**
 * The Summary tab's one Generate button. It opens a small form under itself:
 * the source (the local recording, or the linked Teams meeting's transcript),
 * the summary template, and the output language — the template and language
 * are remembered for the next recap. Templates open as definition files from
 * here: editing a built-in saves a copy (same id, so it replaces the built-in)
 * to the definitions folder; "New from this one" saves a copy under a new id.
 */
export function SummaryGeneratePanel({
  templates: state,
  sessionLanguage,
  hasSummary,
  canUseRecording,
  transcriptUnavailable,
  busy,
  onGenerate,
  onNotice,
}: Props) {
  const [open, setOpen] = useState(false);
  const [fileBusy, setFileBusy] = useState<"edit" | "duplicate" | null>(null);
  const canUseTranscript = transcriptUnavailable === null;
  const [source, setSource] = useState<SummarySource>(
    canUseRecording || !canUseTranscript ? "recording" : "transcript",
  );
  const ref = useRef<HTMLDivElement>(null);
  const { catalog, template, setTemplate, refresh } = state;

  // Keep the source on one that can be used as the session changes.
  useEffect(() => {
    if (source === "recording" && !canUseRecording && canUseTranscript) setSource("transcript");
    if (source === "transcript" && !canUseTranscript && canUseRecording) setSource("recording");
  }, [source, canUseRecording, canUseTranscript]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const languages = catalog?.languages;
  const languageOptions = useMemo(() => {
    const base = (sessionLanguage ?? "").split("-")[0].toLowerCase();
    const own = languages?.find((l) => l.code === base)?.name;
    return [
      { value: "", label: own ? `Meeting language (${own})` : "Meeting language" },
      ...(languages ?? []).map((l) => ({ value: l.code, label: l.name })),
    ];
  }, [languages, sessionLanguage]);

  const list = catalog?.templates ?? [];
  const current = list.find((t) => t.id === template) ?? null;
  const problems = catalog?.problems ?? [];
  const noSource = !canUseRecording && !canUseTranscript;
  const sourceReady = source === "recording" ? canUseRecording : canUseTranscript;

  async function openFile(duplicate: boolean): Promise<void> {
    if (!current || fileBusy) return;
    setFileBusy(duplicate ? "duplicate" : "edit");
    try {
      const res = await api.meetings.summaryTemplateFile(current.id, duplicate);
      await refresh();
      if (duplicate) setTemplate(res.id);
      setOpen(false);
      if (res.workspace && res.workspace_path) {
        openWorkspaceFile(res.workspace.slug, res.workspace_path);
      } else {
        onNotice(
          `${res.created ? "Saved to" : "Its file is"} ${res.folder}/${res.path} — edit it there; the next recap uses it.`,
          "info",
        );
      }
    } catch (e) {
      onNotice(e instanceof Error ? e.message : "Couldn't open the template file.", "error");
    } finally {
      setFileBusy(null);
    }
  }

  function generate(): void {
    if (!sourceReady || busy) return;
    setOpen(false);
    onGenerate(source);
  }

  const label = hasSummary ? "Regenerate" : "Generate";
  const editHint = current
    ? current.source === "file"
      ? `Open ${current.path} in Files`
      : "Save a copy of this built-in template to your definition files and open it.\n" +
        "The copy replaces the built-in until you delete it."
    : undefined;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={busy !== null || noSource}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-tooltip={
          noSource
            ? "Record the meeting, or link its Teams meeting in the Context tab, to generate a summary"
            : "Choose the source, template and language, then generate the summary"
        }
        onClick={() => {
          if (!open) void refresh();
          setOpen((v) => !v);
        }}
        className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
      >
        {busy ? (
          <Loader2 size={12} className="animate-spin" aria-hidden />
        ) : (
          <Sparkles size={12} aria-hidden />
        )}
        {busy === "transcript" ? "Fetching transcript…" : busy ? "Generating…" : label}
        {problems.length > 0 && !busy && (
          <TriangleAlert size={11} className="text-amber-500" aria-hidden />
        )}
        <ChevronDown size={12} className="text-muted" aria-hidden />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Generate summary"
          className="absolute left-0 z-30 mt-1 w-[22rem] rounded-md border border-border bg-surface text-[12px] shadow-lg"
        >
          {/* Source */}
          <div className="border-b border-border px-3 py-2">
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              Source
            </div>
            <div role="radiogroup" aria-label="Summary source" className="grid grid-cols-2 gap-1">
              <SourceOption
                selected={source === "recording"}
                disabled={!canUseRecording}
                hint={canUseRecording ? undefined : "Nothing recorded yet"}
                onSelect={() => setSource("recording")}
                icon={<Mic size={12} aria-hidden />}
                label="Recording"
              />
              <SourceOption
                selected={source === "transcript"}
                disabled={!canUseTranscript}
                hint={transcriptUnavailable ?? "The linked Teams meeting's published transcript"}
                onSelect={() => setSource("transcript")}
                icon={<ScrollText size={12} aria-hidden />}
                label="Teams transcript"
              />
            </div>
          </div>

          {/* Template */}
          <div className="border-b border-border px-3 pt-2">
            <div className="mb-1 flex items-center gap-2">
              <span className="flex-1 text-[11px] font-medium uppercase tracking-wide text-muted">
                Template
              </span>
              <button
                type="button"
                disabled={!current || fileBusy !== null}
                onClick={() => void openFile(false)}
                data-tooltip={editHint}
                className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-accent disabled:opacity-50"
              >
                {fileBusy === "edit" ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden />
                ) : (
                  <Pencil size={11} aria-hidden />
                )}
                Edit
              </button>
              <button
                type="button"
                disabled={!current || fileBusy !== null}
                onClick={() => void openFile(true)}
                data-tooltip="Save a copy of this template under a new name in your definition files, and open it"
                className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-accent disabled:opacity-50"
              >
                {fileBusy === "duplicate" ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden />
                ) : (
                  <CopyPlus size={11} aria-hidden />
                )}
                New from this one
              </button>
            </div>
            <ul
              role="radiogroup"
              aria-label="Summary template"
              className="-mx-1 max-h-52 overflow-y-auto pb-1"
            >
              {!catalog && (
                <li className="flex items-center gap-1.5 px-2 py-1.5 text-muted">
                  <Loader2 size={11} className="animate-spin" aria-hidden /> Loading templates…
                </li>
              )}
              {list.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={t.id === template}
                    onClick={() => setTemplate(t.id)}
                    className={`flex w-full items-start gap-2 rounded px-2 py-1.5 text-left ${
                      t.id === template ? "bg-accent/10" : "hover:bg-bg"
                    }`}
                  >
                    <span
                      aria-hidden
                      className={`mt-[3px] h-2.5 w-2.5 shrink-0 rounded-full border ${
                        t.id === template ? "border-accent bg-accent" : "border-border"
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className={`truncate ${t.id === template ? "text-accent" : ""}`}>
                          {t.name}
                        </span>
                        {t.source === "file" && (
                          <span
                            className="inline-flex shrink-0 items-center gap-0.5 rounded bg-sky-500/10 px-1 text-[10px] text-sky-600 dark:text-sky-400"
                            data-tooltip={
                              t.overrides_builtin
                                ? `${t.path}\nReplaces the built-in template`
                                : (t.path ?? undefined)
                            }
                          >
                            <FileCode2 size={9} aria-hidden />
                            {t.overrides_builtin ? "edited" : "file"}
                          </span>
                        )}
                      </span>
                      {t.description && (
                        <span className="mt-0.5 block text-[11px] leading-snug text-muted">
                          {t.description}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          {problems.length > 0 && (
            <div className="border-b border-border bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
              <div className="mb-0.5 flex items-center gap-1 font-medium">
                <TriangleAlert size={11} aria-hidden />
                {problems.length === 1
                  ? "A template file has a problem"
                  : `${problems.length} problems in template files`}{" "}
                — it's skipped until fixed
              </div>
              <ul className="max-h-24 space-y-0.5 overflow-y-auto">
                {problems.map((p, i) => (
                  <li key={i} className="break-words">
                    <span className="font-mono">{p.path}</span>
                    {p.location ? ` (${p.location})` : ""}: {p.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Language */}
          <div className="border-b border-border px-3 py-2">
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">
              Language
            </div>
            <Select
              value={state.language}
              onChange={state.setLanguage}
              options={languageOptions}
              ariaLabel="Summary language"
              disabled={!catalog}
              size="sm"
              fullWidth
            />
          </div>

          <div className="flex items-center gap-2 px-3 py-2">
            <span className="flex-1 text-[11px] text-muted">
              {hasSummary ? "Replaces the current summary." : null}
            </span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded px-2 py-1 text-muted hover:text-text"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={generate}
              disabled={!sourceReady || busy !== null}
              className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1 text-white disabled:opacity-50"
            >
              <Sparkles size={12} aria-hidden />
              {label}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SourceOption({
  selected,
  disabled,
  hint,
  onSelect,
  icon,
  label,
}: {
  selected: boolean;
  disabled: boolean;
  hint?: string;
  onSelect: () => void;
  icon: ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      onClick={onSelect}
      data-tooltip={hint}
      className={`inline-flex items-center justify-center gap-1.5 rounded border px-2 py-1.5 disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? "border-accent bg-accent/10 text-accent"
          : "border-border text-text hover:bg-bg"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
