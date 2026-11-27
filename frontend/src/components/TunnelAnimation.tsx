// The principle of Mobile access, drawn: your phone talks to this computer
// through a private relay. Requests (accent) ride the tunnel to the computer,
// answers (green) come back, rings breathe around the locked relay. It moves
// only while the tunnel is up, and never for prefers-reduced-motion.

const TUNNEL = "M58 78 C 110 78, 128 44, 180 44 S 250 78, 302 78";
// Three evenly spaced over the 2.6 s cycle; answers set off between requests.
const STAGGER = ["0s", "0.87s", "1.73s"];
const ANSWERS = ["1.3s", "2.17s"];

const ICON = {
  fill: "none",
  strokeWidth: 1.6,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/** A 24x24 outline icon placed at (x, y) and drawn `size` wide. */
function Icon({ x, y, size, className, children }: {
  x: number;
  y: number;
  size: number;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <g transform={`translate(${x} ${y}) scale(${size / 24})`} className={className} {...ICON}>
      {children}
    </g>
  );
}

export function TunnelAnimation({ running }: { running: boolean }) {
  return (
    <figure
      className="tunnel-anim mb-3 rounded-xl border border-border bg-bg px-1 pt-2"
      data-running={running}
    >
      <svg
        viewBox="0 0 360 104"
        className="block w-full h-auto overflow-visible"
        role="img"
        aria-label="Your phone reaches this computer through a private relay: requests go in, answers come back"
      >
        <path className="tunnel-anim-path" d={TUNNEL} />
        {STAGGER.map((delay) => (
          <circle
            key={delay}
            className="tunnel-anim-ring"
            cx="180"
            cy="44"
            r="22"
            style={{ animationDelay: delay }}
          />
        ))}
        <Icon x={30} y={60} size={30} className="stroke-text">
          <rect x="5" y="2" width="14" height="20" rx="2" />
          <path d="M12 18h.01" />
        </Icon>
        <Icon x={298} y={58} size={40} className="stroke-text">
          <path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16" />
        </Icon>
        <Icon x={158} y={18} size={44} className="stroke-muted">
          <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
        </Icon>
        <Icon x={173} y={37} size={14} className="stroke-accent">
          <rect x="3" y="11" width="18" height="11" rx="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </Icon>
        {STAGGER.map((delay) => (
          <circle key={delay} className="tunnel-anim-req" r="3.5" style={{ animationDelay: delay }} />
        ))}
        {ANSWERS.map((delay) => (
          <circle key={delay} className="tunnel-anim-res" r="3" style={{ animationDelay: delay }} />
        ))}
      </svg>
      <figcaption className="flex justify-between px-1.5 pb-1.5 text-[11px] text-muted">
        <span>Your phone</span>
        <span>Private relay</span>
        <span>This computer</span>
      </figcaption>
    </figure>
  );
}
