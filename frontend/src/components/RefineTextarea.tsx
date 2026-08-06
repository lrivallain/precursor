import {
  forwardRef,
  useCallback,
  useRef,
  type ComponentPropsWithRef,
  type KeyboardEvent,
} from "react";

import { useMarkdownEditor } from "../lib/useMarkdownEditor";
import { useRefine } from "../lib/useRefine";
import { MarkdownToolbar } from "./MarkdownToolbar";
import { RefineButton } from "./RefineButton";

type NativeTextareaProps = Omit<
  ComponentPropsWithRef<"textarea">,
  "value" | "onChange"
>;

export interface RefineTextareaProps extends NativeTextareaProps {
  value: string;
  onValueChange: (value: string) => void;
  /** Context hint sent to the backend (e.g. "system_prompt", "note"). */
  refineKind?: string;
  /** Optional freeform steer for the rewrite. */
  refineInstruction?: string;
  /** Classes for the relative wrapper (e.g. "h-full" for full-height fields). */
  containerClassName?: string;
  /**
   * Opt into Markdown affordances: a formatting toolbar above the field plus
   * ⌘/Ctrl shortcuts (B, I, K, E, ⇧X). Only enable it where the value is
   * rendered as Markdown (notes, summaries, issue/comment drafts).
   */
  markdown?: boolean;
}

/**
 * A textarea with a built-in "Refine with AI" affordance in its bottom-right
 * corner. A drop-in for controlled textareas: swap `onChange={e => set(e.target
 * .value)}` for `onValueChange={set}`. The revert state lives only in memory.
 *
 * With `markdown`, it also grows a formatting toolbar and keyboard shortcuts
 * that rewrite the selection in place (see `useMarkdownEditor`).
 */
export const RefineTextarea = forwardRef<HTMLTextAreaElement, RefineTextareaProps>(
  function RefineTextarea(
    {
      value,
      onValueChange,
      refineKind,
      refineInstruction,
      containerClassName,
      markdown,
      className,
      disabled,
      onKeyDown,
      ...rest
    },
    ref,
  ) {
    const { refine, revert, reset, canRevert, busy, error } = useRefine({
      kind: refineKind,
      instruction: refineInstruction,
    });

    // Own the DOM node so the Markdown helpers can read/rewrite the selection,
    // while still honouring a ref the caller passed (e.g. NotesSection).
    const innerRef = useRef<HTMLTextAreaElement | null>(null);
    const setRefs = useCallback(
      (node: HTMLTextAreaElement | null) => {
        innerRef.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) ref.current = node;
      },
      [ref],
    );

    // Formatting is a manual edit, so it invalidates any pending AI revert too.
    const changeAndReset = useCallback(
      (next: string) => {
        if (canRevert) reset();
        onValueChange(next);
      },
      [canRevert, reset, onValueChange],
    );
    const md = useMarkdownEditor(innerRef, changeAndReset);

    const handleClick = async () => {
      if (canRevert) {
        const prev = revert();
        if (prev !== null) onValueChange(prev);
        return;
      }
      const next = await refine(value);
      if (next !== null) onValueChange(next);
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (markdown && md.handleKeyDown(e)) return;
      onKeyDown?.(e);
    };

    // A textarea shows a bottom-right resize grip unless resizing is disabled
    // (the browser default is `resize: both`), so keep the icon clear of it.
    const avoidResizeGrip = !/\bresize-none\b/.test(className ?? "");

    const field = (
      <textarea
        ref={setRefs}
        value={value}
        disabled={disabled || busy}
        onChange={(e) => {
          // A manual edit invalidates the captured original.
          if (canRevert) reset();
          onValueChange(e.target.value);
        }}
        onKeyDown={handleKeyDown}
        // `block` removes the inline-block baseline gap below the textarea so
        // the overlay button's bottom offset aligns with the visible border.
        className={`block ${className ?? ""}`}
        {...rest}
      />
    );

    const button = (
      <RefineButton
        busy={busy}
        canRevert={canRevert}
        error={error}
        avoidResizeGrip={avoidResizeGrip}
        disabled={disabled || (!canRevert && !value.trim())}
        onClick={handleClick}
      />
    );

    if (markdown) {
      return (
        <div className={`flex flex-col ${containerClassName ?? ""}`}>
          <MarkdownToolbar
            onFormat={md.applyFormat}
            disabled={disabled || busy}
            className="mb-1"
          />
          <div className="relative flex min-h-0 flex-1">
            {field}
            {button}
          </div>
        </div>
      );
    }

    return (
      <div className={`relative ${containerClassName ?? ""}`}>
        {field}
        {button}
      </div>
    );
  },
);
