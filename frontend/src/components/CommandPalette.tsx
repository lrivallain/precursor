import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";
import {
  Bot,
  FolderGit2,
  Home,
  MessageSquare,
  MessagesSquare,
  Radio,
  Search,
  SquareKanban,
} from "lucide-react";
import type { SidebarMode } from "./Sidebar";
import { SECTION_COLORS } from "../lib/sections";
import { Modal } from "./Modal";

/**
 * A single jump target in the palette. `mode` drives the icon tint (via
 * SECTION_COLORS) and is undefined for the Home target, which has its own
 * neutral accent.
 */
interface PaletteItem {
  id: string;
  label: string;
  hint: string;
  /** Extra searchable terms so a query can match beyond the visible label. */
  keywords: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  /** Section tint key; omitted for Home. */
  mode?: SidebarMode;
  run: () => void;
}

interface Props {
  onClose: () => void;
  /** Jump to a section (leaves the home launcher). */
  onNavigate: (mode: SidebarMode) => void;
  /** Jump to the home launcher. */
  onGoHome: () => void;
  liveEnabled?: boolean;
  kanbanEnabled?: boolean;
}

/**
 * Keyboard-first section switcher. Opened with ⌘K / Ctrl+K, it lists every
 * section as a flat, filterable list so no section is ever hidden behind the
 * sidebar's horizontal overflow. Type to filter, ↑/↓ to move, Enter to jump,
 * a digit to pick that numbered row, Esc to dismiss.
 */
export function CommandPalette({
  onClose,
  onNavigate,
  onGoHome,
  liveEnabled = true,
  kanbanEnabled = false,
}: Props) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const items = useMemo<PaletteItem[]>(() => {
    const nav = (mode: SidebarMode) => () => {
      onNavigate(mode);
      onClose();
    };
    const all: PaletteItem[] = [
      {
        id: "home",
        label: "Home",
        hint: "Landing launcher",
        keywords: "home start launcher overview",
        icon: Home,
        run: () => {
          onGoHome();
          onClose();
        },
      },
      {
        id: "topics",
        label: "Topics",
        hint: "Long-lived threads",
        keywords: "topics threads conversations history",
        icon: MessagesSquare,
        mode: "topics",
        run: nav("topics"),
      },
      {
        id: "chats",
        label: "Chats",
        hint: "Quick conversations",
        keywords: "chats quick throwaway prompt",
        icon: MessageSquare,
        mode: "chats",
        run: nav("chats"),
      },
      ...(liveEnabled
        ? [
            {
              id: "live",
              label: "Live",
              hint: "Meeting capture",
              keywords: "live meeting transcription notes session",
              icon: Radio,
              mode: "live" as const,
              run: nav("live"),
            },
          ]
        : []),
      {
        id: "workspaces",
        label: "Files",
        hint: "Workspaces & files",
        keywords: "files workspaces folders code",
        icon: FolderGit2,
        mode: "workspaces",
        run: nav("workspaces"),
      },
      {
        id: "agents",
        label: "Agents",
        hint: "Autonomous coding agents",
        keywords: "agents autonomous coding tasks",
        icon: Bot,
        mode: "agents",
        run: nav("agents"),
      },
      ...(kanbanEnabled
        ? [
            {
              id: "kanban",
              label: "Kanban",
              hint: "Issue board",
              keywords: "kanban board issues tracking",
              icon: SquareKanban,
              mode: "kanban" as const,
              run: nav("kanban"),
            },
          ]
        : []),
    ];
    return all;
  }, [liveEnabled, kanbanEnabled, onNavigate, onGoHome, onClose]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        it.label.toLowerCase().includes(q) || it.keywords.includes(q),
    );
  }, [items, query]);

  // Clamp the active row whenever the filtered set shrinks.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Keep the highlighted row scrolled into view for long, filtered lists.
  useEffect(() => {
    const el = listRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function onKeyDown(e: React.KeyboardEvent): void {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (filtered.length ? (a + 1) % filtered.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) =>
        filtered.length ? (a - 1 + filtered.length) % filtered.length : 0,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      filtered[active]?.run();
    } else if (/^[1-9]$/.test(e.key) && !query) {
      // Digit shortcuts jump straight to the Nth row when not mid-search.
      const idx = Number(e.key) - 1;
      if (filtered[idx]) {
        e.preventDefault();
        filtered[idx].run();
      }
    }
  }

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      closeOnBackdrop
      padded
      backdropClassName="bg-black/40"
      panelClassName="bg-bg border border-border rounded-xl shadow-2xl flex flex-col w-full overflow-hidden"
      panelStyle={{ maxWidth: 520, maxHeight: "70vh" }}
      labelledBy="command-palette-input"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 h-12 shrink-0">
        <Search size={16} className="text-muted shrink-0" />
        <input
          id="command-palette-input"
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Jump to a section…"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
          autoComplete="off"
          spellCheck={false}
        />
      </div>

      <ul ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {filtered.length === 0 && (
          <li className="px-3 py-6 text-center text-sm text-muted">
            No matching sections.
          </li>
        )}
        {filtered.map((it, i) => {
          const isActive = i === active;
          const tint = it.mode ? SECTION_COLORS[it.mode].icon : "bg-accent/10 text-accent";
          return (
            <li key={it.id}>
              <button
                type="button"
                onMouseMove={() => setActive(i)}
                onClick={() => it.run()}
                className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left ${
                  isActive ? "bg-surface" : "hover:bg-surface/60"
                }`}
              >
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${tint}`}
                >
                  <it.icon size={16} />
                </span>
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm font-medium">{it.label}</span>
                  <span className="truncate text-[11px] text-muted">{it.hint}</span>
                </span>
                {!query && i < 9 && (
                  <kbd className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted">
                    {i + 1}
                  </kbd>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[10px] text-muted shrink-0">
        <span>↑↓ Navigate</span>
        <span>↵ Open</span>
        <span>1–9 Jump</span>
        <span className="ml-auto">Esc Close</span>
      </div>
    </Modal>
  );
}
