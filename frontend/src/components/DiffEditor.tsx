import { useEffect, useRef } from "react";
import { EDITOR_FONT, languageFor, monaco, wrapsLines } from "../lib/monaco";

/**
 * Monaco's diff editor, read-only: `original` (e.g. HEAD) against `modified`
 * (the working copy). Lazily loaded with the rest of Monaco.
 */
export function DiffEditor({
  path,
  original,
  modified,
  inline,
}: {
  /** Picks the language. */
  path: string;
  original: string;
  modified: string;
  /** One column with removed and added lines interleaved. */
  inline: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneDiffEditor | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const language = languageFor(path);
    // No URI: private models, so they never meet the file's own editor model.
    const originalModel = monaco.editor.createModel(original, language);
    const modifiedModel = monaco.editor.createModel(modified, language);
    const editor = monaco.editor.createDiffEditor(host.current, {
      ...EDITOR_FONT,
      readOnly: true,
      originalEditable: false,
      automaticLayout: true,
      renderSideBySide: !inline,
      // The Side by side / Inline toggle decides, not the pane's width (the
      // caller forces inline on phones).
      useInlineViewWhenSpaceIsLimited: false,
      minimap: { enabled: false },
      // Prose is compared wrapped, as it is edited.
      wordWrap: wrapsLines(path) ? "on" : "off",
      diffWordWrap: "inherit",
      scrollBeyondLastLine: false,
      renderOverviewRuler: true,
      fixedOverflowWidgets: true,
      // Long files: fold what didn't change, like VS Code's review.
      hideUnchangedRegions: { enabled: true },
      padding: { top: 8 },
    });
    editor.setModel({ original: originalModel, modified: modifiedModel });
    editorRef.current = editor;
    return () => {
      editor.dispose();
      originalModel.dispose();
      modifiedModel.dispose();
      editorRef.current = null;
    };
  }, [path, original, modified]);

  useEffect(() => {
    editorRef.current?.updateOptions({ renderSideBySide: !inline });
  }, [inline]);

  return <div ref={host} className="h-full w-full" />;
}
