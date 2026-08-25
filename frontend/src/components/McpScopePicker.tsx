import type { MCPServerStatus } from "../lib/types";

interface Props {
  /** Parsed scope: `null` = every enabled server, `[]` = none at all. */
  value: string[] | null;
  onChange: (next: string[] | null) => void;
  /** The catalogue, from `api.mcp.list(false)`. */
  servers: MCPServerStatus[];
  /** Label for the leading row (defaults to "Servers"). */
  label?: string;
  /** Shown in place of the "nothing enabled" hint when the scope is empty. */
  emptyHint?: string;
}

/**
 * Tri-state MCP server allowlist picker, shared by the workflow step editor and
 * the agent settings drawer.
 *
 * Which servers, not just whether: every attached server's tool schemas are
 * re-sent on every turn, so something that needs one server shouldn't pay for
 * fifteen. The three states are distinct and all reachable — `null` ("All")
 * attaches the whole enabled catalogue, a populated list attaches only those,
 * and an empty list attaches nothing at all (equivalent to switching tools off).
 */
export function McpScopePicker({ value, onChange, servers, label = "Servers", emptyHint }: Props) {
  // `precursor` ignores the Settings → MCP toggle — it's first-party and
  // attaches whenever tools are on — so it's listed regardless of `enabled`.
  // It is not exempt from the scope, though, and is one of the larger
  // catalogues on a normal install, so hiding it would hide a real cost.
  const scopable = servers.filter((s) => s.enabled || s.name === "precursor");
  // The catalogue always has the built-ins in it, so an empty array means the
  // fetch hasn't landed yet — worth distinguishing from "nothing is enabled",
  // which is a state the user has to act on.
  const loaded = servers.length > 0;
  // `precursor` is always on offer, so it can't stand in for a populated
  // catalogue: having only it still means nothing has been enabled.
  const onlyFirstParty = scopable.every((s) => s.name === "precursor");
  // Names in the scope this install can't attach — either not registered here,
  // or registered and switched off. Surfaced rather than silently dropped,
  // because they are meaningful on the machine the scope came from and the
  // save keeps them.
  const missing = (value ?? []).filter((name) => !scopable.some((s) => s.name === name));

  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <span className="text-muted">{label}</span>
      <button
        type="button"
        onClick={() => onChange(null)}
        data-tooltip="Attach every enabled MCP server"
        className={`rounded-lg border px-2 py-0.5 transition ${
          value === null
            ? "border-sky-500/40 bg-sky-500/10 text-sky-500"
            : "border-border text-muted hover:text-fg"
        }`}
      >
        All
      </button>
      {scopable.map((server) => {
        const active = value?.includes(server.name) ?? false;
        return (
          <button
            key={server.name}
            type="button"
            onClick={() =>
              onChange(
                active
                  ? (value ?? []).filter((n) => n !== server.name)
                  : [...(value ?? []), server.name],
              )
            }
            data-tooltip={`${server.tools.length} tool${server.tools.length === 1 ? "" : "s"}`}
            className={`rounded-lg border px-2 py-0.5 transition ${
              active
                ? "border-sky-500/40 bg-sky-500/10 text-sky-500"
                : "border-border text-muted hover:text-fg"
            }`}
          >
            {server.name}
            {server.tools.length > 0 && (
              <span className="ml-1 opacity-70">{server.tools.length}</span>
            )}
          </button>
        );
      })}
      {/* Named by the scope but not attachable here — kept, not dropped, so a
          definition survives the trip between machines. Coloured by cause:
          amber is a switch away from working, red isn't fixable here at all. */}
      {missing.map((name) => {
        const known = servers.some((s) => s.name === name);
        return (
          <button
            key={name}
            type="button"
            onClick={() => onChange((value ?? []).filter((n) => n !== name))}
            data-tooltip={
              known
                ? "Switched off in Settings → MCP, so it won't attach — click to remove"
                : "Not installed here, so it won't attach — click to remove"
            }
            className={`rounded-lg border border-dashed px-2 py-0.5 line-through transition ${
              known
                ? "border-amber-500/50 text-amber-500/80 hover:text-amber-400"
                : "border-red-500/50 text-red-500/80 hover:text-red-400"
            }`}
          >
            {name}
          </button>
        );
      })}
      {/* Nothing but the first-party server to pick from is a setup gap, not an
          empty list: say so instead of leaving a row that looks broken. A
          deliberate empty scope outranks it — that one is a choice. */}
      {value !== null && value.length === 0 ? (
        <span className="text-muted/70">{emptyHint ?? "no tools at all, same as Tools off"}</span>
      ) : (
        loaded &&
        onlyFirstParty && (
          <span className="text-muted/70">
            no other servers enabled — turn them on in Settings → MCP
          </span>
        )
      )}
    </div>
  );
}

/** Parse a stored CSV scope into the picker's tri-state value. */
export function parseScope(raw: string | null | undefined): string[] | null {
  if (raw == null) return null;
  return raw
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

/** Serialise the picker's value back to the stored CSV form. */
export function serializeScope(value: string[] | null): string | null {
  return value === null ? null : value.join(",");
}
