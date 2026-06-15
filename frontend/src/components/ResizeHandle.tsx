interface Props {
  onMouseDown: (e: React.MouseEvent) => void;
  /** Position relative to the panel being resized. */
  side?: "left" | "right";
}

export function ResizeHandle({ onMouseDown, side = "right" }: Props) {
  const sideClass = side === "right" ? "-right-0.5" : "-left-0.5";
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onMouseDown={onMouseDown}
      className={`absolute top-0 ${sideClass} h-full w-1 cursor-col-resize select-none group z-10`}
    >
      <div className="h-full w-px mx-auto bg-transparent group-hover:bg-accent/60 transition-colors" />
    </div>
  );
}
