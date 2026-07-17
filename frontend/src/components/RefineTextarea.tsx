import { forwardRef, type ComponentPropsWithRef } from "react";

import { useRefine } from "../lib/useRefine";
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
}

/**
 * A textarea with a built-in "Refine with AI" affordance in its bottom-right
 * corner. A drop-in for controlled textareas: swap `onChange={e => set(e.target
 * .value)}` for `onValueChange={set}`. The revert state lives only in memory.
 */
export const RefineTextarea = forwardRef<HTMLTextAreaElement, RefineTextareaProps>(
  function RefineTextarea(
    {
      value,
      onValueChange,
      refineKind,
      refineInstruction,
      containerClassName,
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

    const handleClick = async () => {
      if (canRevert) {
        const prev = revert();
        if (prev !== null) onValueChange(prev);
        return;
      }
      const next = await refine(value);
      if (next !== null) onValueChange(next);
    };

    return (
      <div className={`relative ${containerClassName ?? ""}`}>
        <textarea
          ref={ref}
          value={value}
          disabled={disabled || busy}
          onChange={(e) => {
            // A manual edit invalidates the captured original.
            if (canRevert) reset();
            onValueChange(e.target.value);
          }}
          onKeyDown={onKeyDown}
          className={className}
          {...rest}
        />
        <RefineButton
          busy={busy}
          canRevert={canRevert}
          error={error}
          disabled={disabled || (!canRevert && !value.trim())}
          onClick={handleClick}
        />
      </div>
    );
  },
);
