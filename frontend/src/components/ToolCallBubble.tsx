import { useState } from "react";
import { ChevronDown, ChevronRight, AlertCircle, Wrench } from "lucide-react";

interface Props {
  name: string;
  arguments: string;
  content: string | null;
  isError?: boolean;
  pending?: boolean;
}

function tryPrettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

function splitName(qualified: string): { server: string; tool: string } {
  const idx = qualified.indexOf("__");
  if (idx === -1) return { server: "", tool: qualified };
  return { server: qualified.slice(0, idx), tool: qualified.slice(idx + 2) };
}

export function ToolCallBubble({ name, arguments: args, content, isError, pending }: Props) {
  const [open, setOpen] = useState(false);
  const { server, tool } = splitName(name);

  return (
    <div className="w-full">
      <div
        className={`border rounded-lg text-sm ${
          isError
            ? "border-red-500/40 bg-red-500/5"
            : "border-blue-500/40 bg-blue-500/5"
        }`}
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left rounded-lg hover:bg-blue-500/10"
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {isError ? (
            <AlertCircle size={14} className="text-red-500" />
          ) : (
            <Wrench size={14} className="text-blue-500" />
          )}
          <span className="text-[11px] uppercase tracking-wide text-blue-500/80">
            tool{server && ` · ${server}`}
          </span>
          <span className="font-mono text-xs">{tool}</span>
          {pending && (
            <span className="ml-auto text-[11px] text-blue-500 italic">running…</span>
          )}
          {!pending && isError && (
            <span className="ml-auto text-[11px] text-red-500">error</span>
          )}
        </button>
        {open && (
          <div className="px-3 pb-3 space-y-2 border-t border-blue-500/30">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-blue-500/80 mb-1 mt-2">
                Arguments
              </div>
              <pre className="text-xs bg-bg/60 border border-blue-500/20 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
                {tryPrettyJson(args || "{}")}
              </pre>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-blue-500/80 mb-1">
                Result
              </div>
              {pending ? (
                <div className="text-xs text-muted italic">Waiting for result…</div>
              ) : (
                <pre className="text-xs bg-bg/60 border border-blue-500/20 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words max-h-72">
                  {content ?? "(no content)"}
                </pre>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
