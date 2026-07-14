import { X } from "lucide-react";
import { Modal } from "./Modal";
import { Z_INDEX } from "../lib/constants";
import { TopicCreateForm } from "./TopicCreateForm";
import type { Topic, TopicNode } from "../lib/types";

interface Props {
  initialParentId: number | null;
  tree: TopicNode[];
  onClose: () => void;
  onCreated: (topic: Topic) => void;
}

export function TopicCreateModal({ initialParentId, tree, onClose, onCreated }: Props) {
  return (
    <Modal
      onClose={onClose}
      zIndex={Z_INDEX.MODAL}
      panelClassName="w-[min(520px,100%)] bg-bg border border-border rounded-lg shadow-lg flex flex-col"
    >
      <header className="flex items-center justify-between px-4 h-12 border-b border-border">
        <h2 className="font-semibold">New topic</h2>
        <button
          onClick={onClose}
          className="p-1.5 rounded hover:bg-surface"
          aria-label="Close"
          data-tooltip="Close"
        >
          <X size={18} />
        </button>
      </header>

      <div className="p-4">
        <TopicCreateForm
          tree={tree}
          initialParentId={initialParentId}
          onCreated={onCreated}
          onCancel={onClose}
          submitLabel="Create"
        />
      </div>
    </Modal>
  );
}
