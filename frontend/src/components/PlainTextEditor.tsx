import type { CodeEditorProps } from "./CodeEditor";

/**
 * The editor when Monaco's chunk can't be loaded (typically a tab left open
 * across an upgrade, whose chunk names no longer exist): a plain textarea, so
 * the file stays editable.
 */
export function PlainTextEditor({ value, onChange, onSave }: CodeEditorProps) {
  return (
    <textarea
      className="w-full h-full resize-none bg-bg text-text font-mono text-sm p-4 outline-none"
      value={value}
      spellCheck={false}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          onSave(e.currentTarget.value);
        }
      }}
    />
  );
}
