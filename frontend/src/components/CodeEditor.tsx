import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";
import { api } from "../lib/api";
import {
  EDITOR_FONT,
  languageFor,
  loadDefinitionSchemas,
  monaco,
  wrapsLines,
} from "../lib/monaco";
import { isDefinitionFile } from "./DefinitionFileIssues";
import type { LineChange } from "../lib/diffGutter";

/** A finding to underline, e.g. from the definitions check. 1-based, end exclusive. */
export interface EditorMarker {
  severity: "error" | "warning";
  message: string;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
}

export interface CodeEditorHandle {
  /** Move the cursor to a position and scroll it into view. */
  reveal: (line: number, column: number) => void;
}

// The overview ruler's marks for the gutter bars (styles in index.css).
const GUTTER_COLORS = {
  added: "#2ea04399",
  modified: "#3b82f699",
  deleted: "#f8514999",
} as const;

// Kept apart from monaco-yaml's own markers, which it replaces as you type.
const MARKER_OWNER = "precursor-check";

export interface CodeEditorProps {
  /** Identifies the file across editors, e.g. `<workspace slug>/<path>`. */
  modelKey: string;
  /** The file's path, which picks the language. */
  path: string;
  value: string;
  onChange: (value: string) => void;
  /** Called with the buffer as it is now, which may be ahead of `value`. */
  onSave: (value: string) => void;
  onCursorChange?: (line: number, column: number) => void;
  /** Phones: drop the chrome that eats horizontal space. */
  compact?: boolean;
  /** Findings about the file as last saved; they move with later edits. */
  markers?: EditorMarker[];
  /** Lines changed since the last commit, shown as gutter bars. */
  lineChanges?: LineChange[];
  handle?: Ref<CodeEditorHandle | null>;
}

/**
 * Monaco editing one workspace file. Its model lives as long as the component,
 * so keep it mounted (hidden) to preserve undo history across a preview.
 */
export function CodeEditor({
  modelKey,
  path,
  value,
  onChange,
  onSave,
  onCursorChange,
  compact = false,
  markers,
  lineChanges,
  handle,
}: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const [editor, setEditor] = useState<monaco.editor.IStandaloneCodeEditor | null>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const callbacks = useRef({ onChange, onSave, onCursorChange });
  callbacks.current = { onChange, onSave, onCursorChange };
  // The model is created from the value at that moment; later values arrive
  // through the sync effect below.
  const latestValue = useRef(value);
  latestValue.current = value;
  // What the editor reported and the parent may not have rendered yet.
  const emitted = useRef<string[]>([]);

  useEffect(() => {
    let disposed = false;
    let dispose = () => {};
    // A definition file waits for its schema: configuring it later would
    // restart the YAML worker that the first file had just started.
    const ready = isDefinitionFile(path)
      ? loadDefinitionSchemas(api.definitions.schema)
      : Promise.resolve();
    void ready.then(() => {
      if (disposed || !host.current) return;
      dispose = mount(host.current);
    });
    return () => {
      disposed = true;
      dispose();
    };
    // The key identifies the file; the parent remounts us for another one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey]);

  function mount(element: HTMLElement): () => void {
    // A file: URI so monaco-yaml's schema globs match the file's name.
    const uri = monaco.Uri.from({ scheme: "file", path: `/${modelKey}` });
    const model =
      monaco.editor.getModel(uri) ??
      monaco.editor.createModel(latestValue.current, languageFor(path), uri);
    const instance = monaco.editor.create(element, {
      model,
      ...EDITOR_FONT,
      automaticLayout: true,
      minimap: { enabled: false },
      overviewRulerBorder: false,
      scrollBeyondLastLine: false,
      wordWrap: wrapsLines(path) ? "on" : "off",
      // Hovers and suggestions escape the pane instead of being clipped by it.
      fixedOverflowWidgets: true,
      renderLineHighlight: "line",
      tabSize: 2,
      padding: { top: 8 },
      ...(compact
        ? { lineNumbersMinChars: 2, folding: false, glyphMargin: false }
        : {}),
    });
    instance.addAction({
      id: "precursor.save",
      label: "Save file",
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
      run: () => callbacks.current.onSave(model.getValue()),
    });
    const changes = model.onDidChangeContent(() => {
      const text = model.getValue();
      emitted.current.push(text);
      callbacks.current.onChange(text);
    });
    const cursor = instance.onDidChangeCursorPosition((e) =>
      callbacks.current.onCursorChange?.(e.position.lineNumber, e.position.column),
    );
    editorRef.current = instance;
    setEditor(instance);
    return () => {
      changes.dispose();
      cursor.dispose();
      instance.dispose();
      model.dispose();
      editorRef.current = null;
      setEditor(null);
    };
  }

  // The buffer can change outside the editor: a reload after a pull, or the
  // diagram editor writing its XML. As an edit, so it stays undoable.
  useEffect(() => {
    // A value the editor emitted is only its own past: the parent renders
    // behind fast typing, and writing it back would drop the keys since.
    const seen = emitted.current.indexOf(value);
    if (seen !== -1) {
      emitted.current.splice(0, seen + 1);
      return;
    }
    const model = editor?.getModel();
    if (model && model.getValue() !== value) {
      model.pushEditOperations(
        [],
        [{ range: model.getFullModelRange(), text: value }],
        () => null,
      );
    }
  }, [editor, value]);

  useEffect(() => {
    const model = editor?.getModel();
    if (!model) return;
    monaco.editor.setModelMarkers(
      model,
      MARKER_OWNER,
      (markers ?? []).map((m) => ({
        severity:
          m.severity === "error" ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
        message: m.message,
        source: "Precursor check",
        startLineNumber: m.line,
        startColumn: m.column,
        endLineNumber: m.endLine,
        endColumn: m.endColumn,
      })),
    );
  }, [editor, markers]);

  useEffect(() => {
    if (!editor) return;
    const collection = editor.createDecorationsCollection(
      (lineChanges ?? []).map((change) => ({
        range: new monaco.Range(change.start, 1, change.end, 1),
        options: {
          isWholeLine: true,
          linesDecorationsClassName: `precursor-gutter-${change.kind}`,
          overviewRuler: {
            color: GUTTER_COLORS[change.kind],
            position: monaco.editor.OverviewRulerLane.Left,
          },
        },
      })),
    );
    return () => collection.clear();
  }, [editor, lineChanges]);

  useImperativeHandle(
    handle,
    () => ({
      reveal(line, column) {
        const editor = editorRef.current;
        if (!editor) return;
        editor.setPosition({ lineNumber: line, column });
        editor.revealPositionInCenterIfOutsideViewport({ lineNumber: line, column });
        editor.focus();
      },
    }),
    [],
  );

  useEffect(() => {
    editor?.updateOptions(
      compact
        ? { lineNumbersMinChars: 2, folding: false }
        : { lineNumbersMinChars: 5, folding: true },
    );
  }, [editor, compact]);

  return <div ref={host} className="h-full w-full" />;
}
