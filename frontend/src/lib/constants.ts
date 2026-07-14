// Shared, cross-component constants. Only values that are genuinely reused in
// more than one place live here — one-off literals stay local to their module.

/** Windowed-transcript pagination shared by the conversation panels. */
export const PAGINATION = {
  /**
   * Messages fetched per page when windowing a transcript. The first load
   * pulls the most recent page; scrolling toward the top pulls older ones.
   */
  MESSAGE_PAGE_SIZE: 50,
} as const;

/** Timing literals (milliseconds) shared across components. */
export const TIMING = {
  /**
   * Grace window before a soft-deleted message is committed server-side. The
   * conversation panels arm the delete timer and the undo toast off this value,
   * so they must stay in lockstep.
   */
  UNDO_DELETE_MS: 5000,
} as const;

/**
 * Semantic z-index scale, expressed as the exact Tailwind class strings so the
 * JIT scanner still sees them. Higher layers stack above lower ones. Consume as
 * `className={`… ${Z_INDEX.MODAL}`}` instead of hardcoding `z-[…]` literals.
 */
export const Z_INDEX = {
  /** Sticky/raised in-flow chrome (resize handles, pinned headers). */
  RAISED: "z-10",
  /** Inline poppers anchored to a control (pickers, slash-command menu). */
  POPOVER: "z-30",
  /** App sidebar / persistent navigation surfaces. */
  SIDEBAR: "z-40",
  /** Standard modal dialogs and their backdrops. */
  MODAL: "z-50",
  /** Modals that must sit above another modal (nested confirm). */
  MODAL_NESTED: "z-[70]",
  /** Top-most blocking overlays (destructive confirm, audio help). */
  OVERLAY: "z-[80]",
} as const;
