import { useMemo, useRef, useState } from "react";
import { Ban, Search, Smile } from "lucide-react";

/**
 * Emoji picker for the small "icon" fields (workflows today, anything else that
 * wants one later).
 *
 * Three ways in, because no curated list is ever complete:
 *  - **browse** a categorised grid of common emoji;
 *  - **search** it by keyword;
 *  - **type or paste anything** into the custom field — including an emoji from
 *    the OS picker (⌃⌘Space on macOS), so the whole Unicode set is reachable
 *    without shipping a multi-megabyte emoji database.
 *
 * The icon is always optional: **No icon** clears it, and a null icon is a
 * first-class state rather than a fallback glyph.
 */

interface Group {
  name: string;
  /** `emoji keyword keyword …` — the first token is the glyph. */
  items: string[];
}

const GROUPS: Group[] = [
  {
    name: "Work",
    items: [
      "⚙️ gear settings config",
      "🛠️ tools build fix",
      "🔧 wrench fix repair",
      "🧰 toolbox kit",
      "📋 clipboard tasks list",
      "📝 memo write note draft",
      "🗂️ folders organise index",
      "📁 folder files",
      "📎 paperclip attach",
      "🖇️ links attach",
      "📌 pin important",
      "🗓️ calendar schedule plan",
      "⏰ alarm clock reminder time",
      "⏳ hourglass wait pending",
      "🔁 repeat loop recurring",
      "🔔 bell notify alert",
    ],
  },
  {
    name: "Flow",
    items: [
      "🚀 rocket launch ship deploy",
      "🧭 compass navigate direction",
      "🎯 target goal aim",
      "🏁 finish flag done complete",
      "🚦 traffic light gate check",
      "🛡️ shield guard protect safety",
      "✅ check done pass success",
      "❌ cross fail no error",
      "⚠️ warning caution risk",
      "🔍 magnify search inspect review",
      "🧪 test experiment lab",
      "🔬 microscope analyse research",
      "♻️ recycle retry refresh",
      "⏭️ next skip forward",
      "🧱 brick build block",
      "🪜 ladder steps stages",
    ],
  },
  {
    name: "Content",
    items: [
      "📊 chart report analytics data",
      "📈 up trend growth",
      "📉 down trend decline",
      "📄 page document file",
      "📚 books docs library",
      "🗞️ news article press",
      "✉️ envelope email mail send",
      "📣 megaphone announce broadcast",
      "💬 speech chat comment",
      "🗒️ notepad notes",
      "🧾 receipt invoice billing",
      "🔖 bookmark tag label",
      "🖼️ picture image media",
      "🎬 clapper video film",
      "🎧 headphones audio listen",
      "🌐 globe web internet",
    ],
  },
  {
    name: "People & AI",
    items: [
      "🤖 robot agent bot ai",
      "🧠 brain think memory smart",
      "👤 person user human",
      "👥 people team group",
      "🧑‍💻 developer coder engineer",
      "🕵️ detective investigate audit",
      "👀 eyes watch review look",
      "🤝 handshake agree approve deal",
      "🙋 raise hand ask question",
      "💡 bulb idea insight",
      "🗣️ speaking voice say",
      "✍️ writing hand author",
    ],
  },
  {
    name: "Systems",
    items: [
      "💻 laptop computer",
      "🖥️ desktop monitor screen",
      "🗄️ cabinet archive storage",
      "💾 disk save backup",
      "🗃️ card box records",
      "🔌 plug connect integration",
      "🔗 link chain url",
      "🔑 key secret auth access",
      "🔒 lock secure private",
      "🧩 puzzle plugin extension module",
      "📦 package release bundle",
      "☁️ cloud remote hosted",
      "🛰️ satellite monitor telemetry",
      "🐛 bug defect issue",
      "🔥 fire urgent hot incident",
      "⚡ zap fast trigger action",
    ],
  },
  {
    name: "Nature & misc",
    items: [
      "🌱 seedling new grow start",
      "🌳 tree branch",
      "🍀 clover luck",
      "🌊 wave flow stream",
      "🌙 moon night nightly",
      "☀️ sun day daily morning",
      "⭐ star favourite highlight",
      "✨ sparkles magic new shiny",
      "🎉 party celebrate done launch",
      "🏆 trophy win best",
      "🧊 ice cold freeze",
      "🎲 dice random chance",
    ],
  },
];

interface Props {
  /** Current icon, or null when none is set. */
  value: string | null;
  onChange: (icon: string | null) => void;
  /** Rendered when no icon is set (defaults to a neutral placeholder glyph). */
  placeholder?: string;
}

export function EmojiPicker({ value, onChange, placeholder }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [custom, setCustom] = useState("");
  const popoverRef = useRef<HTMLDivElement | null>(null);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return GROUPS.map((g) => ({
      name: g.name,
      // Match on any keyword *or* the glyph itself, so pasting an emoji into the
      // search box finds it in the grid.
      items: g.items
        .filter((entry) => !q || entry.toLowerCase().includes(q))
        .map((entry) => entry.split(" ")[0]),
    })).filter((g) => g.items.length > 0);
  }, [query]);

  function choose(icon: string | null): void {
    onChange(icon);
    setOpen(false);
    setQuery("");
    setCustom("");
  }

  /** Take the first glyph of whatever was typed/pasted, so "🎈 balloon" works. */
  function commitCustom(): void {
    const first = [...custom.trim()][0];
    if (first) choose(first);
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={value ? "Change icon" : "Choose an icon"}
        className={`flex h-9 w-9 items-center justify-center rounded-lg border text-lg transition ${
          open
            ? "border-indigo-500 bg-indigo-500/10"
            : "border-border hover:border-indigo-500/50 hover:bg-white/5"
        }`}
      >
        {value ?? (
          <span className="text-muted">
            {placeholder ? (
              <span className="text-lg opacity-40">{placeholder}</span>
            ) : (
              <Smile size={16} />
            )}
          </span>
        )}
      </button>

      {open && (
        <>
          {/* Click-away. Rendered behind the panel so it can't swallow its clicks. */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            role="presentation"
          />
          <div
            ref={popoverRef}
            role="dialog"
            aria-label="Choose an icon"
            className="absolute left-0 top-11 z-50 w-72 rounded-xl border border-border bg-surface p-2 shadow-2xl"
          >
            <div className="mb-2 flex items-center gap-1.5 rounded-lg border border-border bg-bg/40 px-2">
              <Search size={13} className="shrink-0 text-muted" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search icons…"
                className="w-full bg-transparent py-1.5 text-xs text-fg outline-none placeholder:text-muted/70"
              />
            </div>

            <div className="max-h-56 overflow-y-auto pr-1">
              {results.length === 0 && (
                <p className="px-1 py-4 text-center text-[11px] text-muted">
                  Nothing matches. Paste any emoji below to use it.
                </p>
              )}
              {results.map((g) => (
                <div key={g.name} className="mb-2">
                  <p className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wide text-muted">
                    {g.name}
                  </p>
                  <div className="flex flex-wrap gap-0.5">
                    {g.items.map((e) => (
                      <button
                        key={e}
                        type="button"
                        onClick={() => choose(e)}
                        className={`h-8 w-8 rounded-lg text-lg transition ${
                          value === e
                            ? "bg-indigo-500/20 ring-1 ring-indigo-500/50"
                            : "hover:bg-white/5"
                        }`}
                      >
                        {e}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-2 space-y-2 border-t border-border pt-2">
              {/* Anything the curated list doesn't cover — including whatever the
                  OS emoji picker inserts. */}
              <div className="flex items-center gap-1.5">
                <input
                  value={custom}
                  onChange={(e) => setCustom(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      commitCustom();
                    }
                  }}
                  placeholder="Or paste any emoji…"
                  className="w-full rounded-lg border border-border bg-bg/40 px-2 py-1.5 text-xs text-fg outline-none focus:border-indigo-500"
                />
                <button
                  type="button"
                  onClick={commitCustom}
                  disabled={!custom.trim()}
                  className="rounded-lg bg-indigo-500 px-2 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-600 disabled:opacity-40"
                >
                  Use
                </button>
              </div>

              <button
                type="button"
                onClick={() => choose(null)}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-border px-2 py-1.5 text-xs text-muted transition hover:border-red-500/40 hover:text-red-500"
              >
                <Ban size={12} /> No icon
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
