import { useEffect, useRef, useState } from "react";

/**
 * A title that becomes an inline text field on double-click, commits on Enter
 * or blur, and cancels on Escape. Shared by the topic tree and the chat list so
 * renaming feels identical everywhere.
 */
export function InlineTitle({
  title,
  onRename,
  className,
  inputClassName,
}: {
  title: string;
  onRename: (next: string) => void | Promise<void>;
  className?: string;
  inputClassName?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);
  // Guards against committing twice (Enter triggers a blur right after).
  const activeRef = useRef(false);

  useEffect(() => {
    setDraft(title);
  }, [title]);

  useEffect(() => {
    if (!editing) return;
    activeRef.current = true;
    const el = inputRef.current;
    if (el) {
      el.focus();
      el.select();
    }
  }, [editing]);

  async function commit(): Promise<void> {
    if (!activeRef.current) return;
    activeRef.current = false;
    setEditing(false);
    const next = draft.trim();
    if (next && next !== title) {
      try {
        await onRename(next);
      } catch {
        setDraft(title);
      }
    } else {
      setDraft(title);
    }
  }

  function cancel(): void {
    activeRef.current = false;
    setEditing(false);
    setDraft(title);
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") {
            e.preventDefault();
            void commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
        }}
        onBlur={() => void commit()}
        className={
          inputClassName ??
          "min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1 py-0 text-sm outline-none"
        }
      />
    );
  }

  return (
    <span
      className={className}
      onDoubleClick={(e) => {
        e.stopPropagation();
        setDraft(title);
        setEditing(true);
      }}
      title="Double-click to rename"
    >
      {title}
    </span>
  );
}
