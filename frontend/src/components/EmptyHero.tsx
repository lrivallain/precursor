/** Centered logo + caption shown when a section has nothing selected. */
export function EmptyHero({ label }: { label: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-muted gap-3">
      <img
        src="/logo.svg"
        alt=""
        aria-hidden="true"
        width={72}
        height={72}
        className="rounded-2xl opacity-90"
      />
      <span>{label}</span>
    </div>
  );
}
