import { useId, useRef, useState } from "react";
import { Plus, X } from "lucide-react";

export function DeploymentNamesInput({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const names = [...new Set(value.split(/[,\n]/).map((name) => name.trim()).filter(Boolean))];

  function addModel() {
    const name = draft.trim();
    if (!name) return;
    if (/[,\n]/.test(name)) {
      setError("Add one deployment name at a time, without commas or newlines.");
      inputRef.current?.focus();
      return;
    }
    if (names.includes(name)) {
      setError("This deployment is already added.");
      inputRef.current?.focus();
      return;
    }
    onChange([...names, name].join(", "));
    setDraft("");
    setError("");
    inputRef.current?.focus();
  }

  return (
    <div data-deployment-names>
      <label htmlFor={id} className="block text-xs text-muted mb-1">Deployment names</label>
      {names.length > 0 && (
        <ul aria-label="Configured deployments" className="flex flex-wrap gap-1.5 mb-2">
          {names.map((name, index) => (
            <li key={name} className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-bg pl-2.5 pr-1 py-0.5 text-xs">
              <span className="min-w-0 break-all" data-tooltip={name}>{name}</span>
              {index === 0 && <span className="text-muted text-[10px] shrink-0">Default</span>}
              <button
                type="button"
                aria-label={`Remove deployment ${name}`}
                data-tooltip={`Remove ${name}`}
                onClick={() => {
                  onChange(names.filter((entry) => entry !== name).join(", "));
                  setError("");
                  inputRef.current?.focus();
                }}
                className="shrink-0 rounded-full p-1 text-muted hover:bg-surface hover:text-text focus-visible:outline-2 focus-visible:outline-accent"
              >
                <X size={12} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input
          ref={inputRef}
          id={id}
          value={draft}
          onChange={(event) => { setDraft(event.target.value); setError(""); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.stopPropagation();
              addModel();
            }
          }}
          placeholder="Enter a deployment name"
          aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`}
          aria-invalid={Boolean(error)}
          className="min-w-0 flex-1 bg-bg border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
        />
        <button
          type="button"
          onClick={addModel}
          disabled={!draft.trim()}
          className="shrink-0 inline-flex items-center gap-1 rounded border border-border px-2 py-1.5 text-xs hover:bg-bg disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-accent"
        >
          <Plus size={12} aria-hidden="true" />
          Add model
        </button>
      </div>
      <p id={`${id}-help`} className="text-[11px] text-muted mt-1">
        Add each deployment with Enter or Add model. The first is the default.
      </p>
      {error && <p id={`${id}-error`} role="alert" className="text-xs text-red-500 mt-1">{error}</p>}
    </div>
  );
}
