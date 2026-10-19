import { Modal } from "./Modal";
import { SHORTCUT_GROUPS } from "../lib/shortcuts";

interface Props {
  onClose: () => void;
}

/** Cheat-sheet of the keyboard shortcuts, opened with `?` or from ⌘K. */
export function ShortcutsHelp({ onClose }: Props) {
  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      labelledBy="shortcuts-title"
      panelClassName="w-full max-w-md rounded-2xl border border-border bg-bg p-5 shadow-xl"
    >
      <h2 id="shortcuts-title" className="text-base font-semibold">
        Keyboard shortcuts
      </h2>
      <div className="mt-4 flex flex-col gap-5">
        {SHORTCUT_GROUPS.map((group) => (
          <section key={group.title}>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">
              {group.title}
            </h3>
            <ul className="mt-2 flex flex-col gap-1.5">
              {group.entries.map((entry) => (
                <li
                  key={entry.description}
                  className="flex items-center justify-between gap-4 text-sm"
                >
                  <span>{entry.description}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {entry.keys.map((key) => (
                      <kbd
                        key={key}
                        className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-muted"
                      >
                        {key}
                      </kbd>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  );
}
