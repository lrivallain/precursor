// The principle of Mobile access, drawn for the current setup: your phone talks
// to this computer through a private relay. It shows the missing piece (no CLI,
// not signed in), a tunnel waiting for its first phone (the computer's side is
// live, the phone's side waits for a scan), or a working tunnel (requests in
// accent, answers in green, rings around the locked relay). Motion only where
// something is happening, and never for prefers-reduced-motion.
import type { TunnelScene, TunnelSceneMode } from "../lib/mobileAccess";

const LEFT = "M58 78 C 110 78, 128 44, 180 44";
const RIGHT = "M180 44 C 232 44, 250 78, 302 78";
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

/** A round badge with a glyph, pinned to an icon's corner. */
function Badge({ x, y, tone, glyph, pulse = false }: {
  x: number;
  y: number;
  tone: "warn" | "error" | "ok" | "accent";
  glyph: "!" | "qr" | "check";
  pulse?: boolean;
}) {
  return (
    <g className={`tunnel-anim-badge is-${tone}${pulse ? " is-pulse" : ""}`} transform={`translate(${x} ${y})`}>
      <circle r="8" />
      {glyph === "!" && (
        <>
          <path d="M0 -4 V1" />
          <path d="M0 4 h.01" />
        </>
      )}
      {glyph === "check" && <path d="M-3.5 0.5 l2.5 2.5 4.5-5" />}
      {glyph === "qr" && (
        <>
          <rect x="-4" y="-4" width="3" height="3" />
          <rect x="1" y="-4" width="3" height="3" />
          <rect x="-4" y="1" width="3" height="3" />
          <path d="M1.5 2.5 h2.5" />
        </>
      )}
    </g>
  );
}

function Relay({ mode }: { mode: TunnelSceneMode }) {
  const missing = mode === "missing";
  return (
    <>
      <Icon
        x={158}
        y={18}
        size={44}
        className={missing ? "tunnel-anim-missing" : mode === "off" ? "stroke-muted opacity-60" : "stroke-muted"}
      >
        <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
      </Icon>
      {(mode === "starting" || mode === "waiting" || mode === "live") && (
        <Icon x={173} y={37} size={14} className="stroke-accent">
          <rect x="3" y="11" width="18" height="11" rx="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </Icon>
      )}
      {missing && <Badge x={196} y={26} tone="warn" glyph="!" pulse />}
      {mode === "error" && <Badge x={196} y={26} tone="error" glyph="!" />}
    </>
  );
}

function Phone({ mode }: { mode: TunnelSceneMode }) {
  const waiting = mode === "waiting";
  const dim = mode === "missing" || mode === "off" || mode === "error";
  return (
    <>
      <Icon
        x={30}
        y={60}
        size={30}
        className={waiting ? "tunnel-anim-ghost" : dim ? "stroke-text opacity-50" : "stroke-text"}
      >
        <rect x="5" y="2" width="14" height="20" rx="2" />
        <path d="M12 18h.01" />
      </Icon>
      {waiting && <Badge x={58} y={62} tone="accent" glyph="qr" pulse />}
      {mode === "live" && <Badge x={58} y={62} tone="ok" glyph="check" />}
    </>
  );
}

/** What travels: both ways when live, the computer's side only while waiting. */
function Traffic({ mode }: { mode: TunnelSceneMode }) {
  if (mode === "live") {
    return (
      <>
        {STAGGER.map((delay) => (
          <circle key={delay} className="tunnel-anim-req" r="3.5" style={{ animationDelay: delay }} />
        ))}
        {ANSWERS.map((delay) => (
          <circle key={delay} className="tunnel-anim-res" r="3" style={{ animationDelay: delay }} />
        ))}
      </>
    );
  }
  if (mode === "waiting") {
    return (
      <>
        <circle className="tunnel-anim-beat" r="3" />
        <circle className="tunnel-anim-beat is-back" r="3" style={{ animationDelay: "1.1s" }} />
      </>
    );
  }
  return null;
}

export function TunnelAnimation({ scene }: { scene: TunnelScene }) {
  const { mode } = scene;
  const relayUp = mode === "starting" || mode === "waiting" || mode === "live";
  return (
    <figure className="tunnel-anim mb-3 rounded-xl border border-border bg-bg px-1 pt-2" data-mode={mode}>
      <svg
        viewBox="0 0 360 104"
        className="block w-full h-auto overflow-visible"
        role="img"
        aria-label={`${scene.phone}, ${scene.relay}, ${scene.computer}`}
      >
        <path className={`tunnel-anim-path${mode === "live" ? "" : " is-faint"}`} d={LEFT} />
        <path className={`tunnel-anim-path${relayUp ? "" : " is-faint"}`} d={RIGHT} />
        {relayUp &&
          STAGGER.map((delay) => (
            <circle
              key={delay}
              className="tunnel-anim-ring"
              cx="180"
              cy="44"
              r="22"
              style={{ animationDelay: delay }}
            />
          ))}
        <Phone mode={mode} />
        <Icon x={298} y={58} size={40} className="stroke-text">
          <path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16" />
        </Icon>
        <Relay mode={mode} />
        <Traffic mode={mode} />
      </svg>
      <figcaption className="flex justify-between px-1.5 pb-1.5 text-[11px] text-muted">
        <span>{scene.phone}</span>
        <span className={mode === "missing" ? "tunnel-anim-warn-text" : undefined}>{scene.relay}</span>
        <span>{scene.computer}</span>
      </figcaption>
    </figure>
  );
}
