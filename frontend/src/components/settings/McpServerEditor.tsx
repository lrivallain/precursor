import { useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { api } from "../../lib/api";
import type { MCPServerStatus } from "../../lib/types";
import {
  authFormFields,
  mcpServerPayload,
  removeAt,
  replaceAt,
  type HeaderRow,
  type McpServerForm,
  type McpTransport,
} from "./mcpServerForm";
import { AuthFields, DetectAuthButton, useAuthDetection } from "./McpServerAuthFields";

const INPUT = "w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent";
const ROW_INPUT = "flex-1 bg-surface border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent";

function initialForm(server: MCPServerStatus | null): McpServerForm {
  return {
    name: server?.name ?? "",
    transport: (server?.transport as McpTransport | undefined) ?? "streamable_http",
    url: server?.url ?? "",
    command: server?.command_bin ?? "",
    args: server?.args ?? [],
    // Existing header values are not exposed by the API for security; keys are
    // shown so the user knows what is set, but values must be re-entered.
    headers: server?.header_keys?.map((k) => ({ key: k, value: "" })) ?? [],
    ...authFormFields(server),
  };
}

export function McpServerEditor({
  server,
  onClose,
  onSaved,
}: {
  server: MCPServerStatus | null;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const isNew = server === null;
  const [form, setForm] = useState(() => initialForm(server));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const update = (patch: Partial<McpServerForm>): void => setForm((prev) => ({ ...prev, ...patch }));

  async function handleSave(): Promise<void> {
    setError(null);
    setSaving(true);
    try {
      const payload = mcpServerPayload(form);
      if (isNew) await api.mcp.create(payload);
      else if (server?.id != null) await api.mcp.update(server.id, payload);
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bg border border-border rounded-lg w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between p-3 border-b border-border">
          <h3 className="text-sm font-medium">
            {isNew ? "New MCP server" : `Edit ${server?.name}`}
          </h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-surface text-muted">
            <X size={16} />
          </button>
        </header>
        <div className="p-3 space-y-3 overflow-y-auto">
          <div>
            <label className="block text-xs text-muted mb-1">Name</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => update({ name: e.target.value })}
              placeholder="my-server"
              className={INPUT}
            />
            <p className="text-[10px] text-muted mt-1">
              Lowercase letters, digits, hyphens. Max 64 chars.
            </p>
          </div>
          <TransportPicker value={form.transport} onChange={(transport) => update({ transport })} />
          {form.transport === "streamable_http" ? (
            <HttpFields
              form={form}
              update={update}
              hiddenValues={!isNew && Boolean(server?.header_keys?.length)}
            />
          ) : (
            <StdioFields form={form} update={update} />
          )}
          {error && (
            <div className="text-xs text-red-500 whitespace-pre-wrap break-words">
              {error}
            </div>
          )}
        </div>
        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving..." : isNew ? "Create" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function TransportPicker({ value, onChange }: { value: McpTransport; onChange: (next: McpTransport) => void }) {
  const option = (transport: McpTransport, label: string) => (
    <label className="flex items-center gap-1 cursor-pointer">
      <input
        type="radio"
        name="transport"
        checked={value === transport}
        onChange={() => onChange(transport)}
        className="accent-accent"
      />
      {label}
    </label>
  );
  return (
    <div>
      <label className="block text-xs text-muted mb-1">Transport</label>
      <div className="flex gap-3 text-xs">
        {option("streamable_http", "HTTP (streamable)")}
        {option("stdio", "stdio (subprocess)")}
      </div>
    </div>
  );
}

type FieldsProps = { form: McpServerForm; update: (patch: Partial<McpServerForm>) => void };

function HttpFields({ form, update, hiddenValues }: FieldsProps & { hiddenValues: boolean }) {
  const setHeaders = (headers: HeaderRow[]): void => update({ headers });
  const detection = useAuthDetection(form, update);
  return (
    <>
      <div>
        <label className="block text-xs text-muted mb-1">URL</label>
        <div className="flex gap-1">
          <input
            type="text"
            value={form.url}
            onChange={(e) => update({ url: e.target.value })}
            onBlur={detection.detectOnce}
            placeholder="https://example.com/mcp"
            className={`${INPUT} font-mono`}
          />
          <DetectAuthButton
            busy={detection.busy}
            disabled={!form.url.trim()}
            onClick={() => void detection.detect()}
          />
        </div>
      </div>
      <AuthFields form={form} update={update} message={detection.message} />
      <RowList
        label="Headers"
        addLabel="+ Add header"
        onAdd={() => setHeaders([...form.headers, { key: "", value: "" }])}
        note={hiddenValues && (
          <p className="text-[10px] text-muted mb-1">
            Existing header values are hidden — re-enter them to keep
            them, or remove the row to drop them.
          </p>
        )}
      >
        {form.headers.map((h, i) => (
          <div key={i} className="flex gap-1">
            <input
              type="text"
              value={h.key}
              onChange={(e) => setHeaders(replaceAt(form.headers, i, { ...h, key: e.target.value }))}
              placeholder="Header-Name"
              className={ROW_INPUT}
            />
            <input
              type="text"
              value={h.value}
              onChange={(e) => setHeaders(replaceAt(form.headers, i, { ...h, value: e.target.value }))}
              placeholder="value"
              className={ROW_INPUT}
            />
            <RemoveRow label="Remove header" onClick={() => setHeaders(removeAt(form.headers, i))} />
          </div>
        ))}
      </RowList>
    </>
  );
}

function StdioFields({ form, update }: FieldsProps) {
  const setArgs = (args: string[]): void => update({ args });
  return (
    <>
      <div>
        <label className="block text-xs text-muted mb-1">Command</label>
        <input
          type="text"
          value={form.command}
          onChange={(e) => update({ command: e.target.value })}
          placeholder="npx"
          className={`${INPUT} font-mono`}
        />
      </div>
      <RowList label="Arguments" addLabel="+ Add arg" onAdd={() => setArgs([...form.args, ""])}>
        {form.args.map((a, i) => (
          <div key={i} className="flex gap-1">
            <input
              type="text"
              value={a}
              onChange={(e) => setArgs(replaceAt(form.args, i, e.target.value))}
              className={ROW_INPUT}
            />
            <RemoveRow label="Remove" onClick={() => setArgs(removeAt(form.args, i))} />
          </div>
        ))}
      </RowList>
    </>
  );
}

function RowList({
  label,
  addLabel,
  onAdd,
  note,
  children,
}: {
  label: string;
  addLabel: string;
  onAdd: () => void;
  /** Shown between the heading and the rows. */
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="block text-xs text-muted">{label}</label>
        <button type="button" onClick={onAdd} className="text-[10px] text-accent hover:underline">
          {addLabel}
        </button>
      </div>
      {note}
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function RemoveRow({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="p-1 rounded hover:bg-surface text-muted hover:text-red-500"
      aria-label={label}
      data-tooltip="Remove"
    >
      <X size={12} />
    </button>
  );
}
