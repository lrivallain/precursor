import { useState } from "react";
import { Check, Link } from "lucide-react";

export function CopyMessageLink({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(new URL(path, window.location.origin).href);
      setError(null);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setError("Could not copy the message link. Check clipboard permissions and try again.");
    }
  }
  return (
    <>
      <button
        type="button"
        onClick={() => void copy()}
        className="p-1 rounded-full text-muted hover:text-accent"
        aria-label={copied ? "Message link copied" : "Copy link to message"}
        data-tooltip={copied ? "Link copied" : "Copy link to message"}
      >
        {copied ? <Check size={12} /> : <Link size={12} />}
      </button>
      {error && <span role="alert" className="text-xs text-red-500">{error}</span>}
    </>
  );
}
