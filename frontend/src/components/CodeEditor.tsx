import { useEffect, useRef } from "react";
import { EDITOR_FONT, languageFor, monaco, wrapsLines } from "../lib/monaco";

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
}: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const callbacks = useRef({ onChange, onSave, onCursorChange });
  callbacks.current = { onChange, onSave, onCursorChange };
  // Read on mount only: later values arrive through the sync effect below.
  const initialValue = useRef(value);
  // What the editor reported and the parent may not have rendered yet.
  const emitted = useRef<string[]>([]);

  useEffect(() => {
    if (!host.current) return;
    // A file: URI so monaco-yaml's schema globs match the file's name.
    const uri = monaco.Uri.from({ scheme: "file", path: `/${modelKey}` });
    const model =
      monaco.editor.getModel(uri) ??
      monaco.editor.createModel(initialValue.current, languageFor(path), uri);
    const editor = monaco.editor.create(host.current, {
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
    editor.addAction({
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
    const cursor = editor.onDidChangeCursorPosition((e) =>
      callbacks.current.onCursorChange?.(e.position.lineNumber, e.position.column),
    );
    editorRef.current = editor;
    return () => {
      changes.dispose();
      cursor.dispose();
      editor.dispose();
      model.dispose();
      editorRef.current = null;
    };
    // The key identifies the file; the parent remounts us for another one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey]);

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
    const model = editorRef.current?.getModel();
    if (model && model.getValue() !== value) {
      model.pushEditOperations(
        [],
        [{ range: model.getFullModelRange(), text: value }],
        () => null,
      );
    }
  }, [value]);

  useEffect(() => {
    editorRef.current?.updateOptions(
      compact
        ? { lineNumbersMinChars: 2, folding: false }
        : { lineNumbersMinChars: 5, folding: true },
    );
  }, [compact]);

  return <div ref={host} className="h-full w-full" />;
}
