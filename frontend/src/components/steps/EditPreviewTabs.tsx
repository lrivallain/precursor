export type EditPreview = "edit" | "preview";

/** The Edit / Preview switch, meant to sit right above the content it switches. */
export function EditPreviewTabs({
  view,
  onChange,
  label,
}: {
  view: EditPreview;
  onChange: (view: EditPreview) => void;
  label: string;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-border text-xs" role="tablist" aria-label={label}>
      {(["edit", "preview"] as const).map((v) => (
        <button
          key={v}
          type="button"
          role="tab"
          aria-selected={view === v}
          onClick={() => onChange(v)}
          className={`px-3 py-1.5 ${view === v ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface"}`}
        >
          {v === "edit" ? "Edit" : "Preview"}
        </button>
      ))}
    </div>
  );
}
