import { useEffect, useState } from "react";
import { Settings as SettingsIcon } from "lucide-react";
import { Sidebar } from "./components/Sidebar";
import { ChatPanel } from "./components/ChatPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { api } from "./lib/api";
import type { Topic, TopicNode } from "./lib/types";

export default function App() {
  const [tree, setTree] = useState<TopicNode[]>([]);
  const [activeTopic, setActiveTopic] = useState<Topic | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  async function refreshTree(): Promise<void> {
    setTree(await api.topicTree());
  }

  useEffect(() => {
    void refreshTree();
  }, []);

  async function handleSelect(id: number): Promise<void> {
    setActiveTopic(await api.getTopic(id));
  }

  async function handleCreate(parentId: number | null): Promise<void> {
    const title = window.prompt("Topic title?");
    if (!title) return;
    const created = await api.createTopic({ title, parent_id: parentId });
    await refreshTree();
    setActiveTopic(created);
  }

  return (
    <div className="flex h-full w-full bg-bg text-text">
      <Sidebar
        tree={tree}
        activeId={activeTopic?.id ?? null}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        onSelect={handleSelect}
        onCreate={handleCreate}
        onRefresh={refreshTree}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <div className="truncate font-medium">
            {activeTopic ? activeTopic.title : "Select or create a topic"}
          </div>
          <button
            className="p-2 rounded hover:bg-surface"
            aria-label="Open settings"
            onClick={() => setSettingsOpen(true)}
          >
            <SettingsIcon size={18} />
          </button>
        </header>

        <div className="flex-1 min-h-0">
          {activeTopic ? (
            <ChatPanel topic={activeTopic} onTopicUpdated={refreshTree} />
          ) : (
            <div className="h-full flex items-center justify-center text-muted">
              No topic selected.
            </div>
          )}
        </div>
      </main>

      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
