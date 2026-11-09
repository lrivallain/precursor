import { FONT_OPTIONS } from "../../lib/font";
import { FONT_PREVIEW_STACKS } from "./panelConfig";
import type { AppearanceSettings } from "./useAppearanceSettings";

export function AppearanceTab({ s }: { s: AppearanceSettings }) {
  const {
    theme,
    setThemeState,
    font,
    setFontState,
    showQuickActions,
    setShowQuickActions,
  } = s;
  return (
    <>
      <section>
        <h3 className="text-sm font-medium mb-2">Theme</h3>
        <div className="flex gap-2">
          {(["light", "dark", "system"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setThemeState(t)}
              className={`px-3 py-1.5 rounded border text-sm capitalize ${
                theme === t ? "border-accent text-accent" : "border-border text-text"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3 className="text-sm font-medium mb-2">Reading font</h3>
        <p className="text-[11px] text-muted mb-2">
          Applies to the whole app. Includes fonts designed to help
          dyslexic or low-vision readers.
        </p>
        <div className="flex flex-col gap-2">
          {FONT_OPTIONS.map((opt) => (
            <label
              key={opt.id}
              className="flex items-start gap-2 cursor-pointer"
            >
              <input
                type="radio"
                name="font_family"
                checked={font === opt.id}
                onChange={() => setFontState(opt.id)}
                className="mt-0.5 accent-accent"
              />
              <span>
                <span
                  className="block text-sm"
                  style={{ fontFamily: FONT_PREVIEW_STACKS[opt.id] }}
                >
                  {opt.label}
                </span>
                <span className="block text-[11px] text-muted">
                  {opt.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </section>
      <section>
        <h3 className="text-sm font-medium mb-2">Quick actions</h3>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showQuickActions}
            onChange={(e) => setShowQuickActions(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block text-sm">
              Show the floating quick-actions button
            </span>
            <span className="block text-[11px] text-muted">
              The draggable Precursor button that creates chats,
              topics, agents, plans and more from any screen. Hiding it
              leaves the usual creation buttons in each section.
            </span>
          </span>
        </label>
      </section>
    </>
  );
}
