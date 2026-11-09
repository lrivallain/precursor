import { NumberInput } from "./NumberInput";
import type { SystemSettings } from "./systemSettings";

type SetSys = (next: SystemSettings) => void;

// Field renderers bound to the current settings object.
function fieldBuilders(sys: SystemSettings, setSys: SetSys) {
  const up = (patch: Partial<SystemSettings>) => setSys({ ...sys, ...patch });
  const numField = (
    label: string,
    key: keyof SystemSettings,
    opts: { min: number; max: number; help?: string },
  ) => (
    <div>
      <label className="block text-xs text-muted mb-1">{label}</label>
      <NumberInput
        value={sys[key] as number}
        min={opts.min}
        max={opts.max}
        onCommit={(n) => up({ [key]: n } as Partial<SystemSettings>)}
      />
      {opts.help && <p className="text-[11px] text-muted mt-1">{opts.help}</p>}
    </div>
  );
  const textField = (label: string, key: keyof SystemSettings, help?: string) => (
    <div>
      <label className="block text-xs text-muted mb-1">{label}</label>
      <input
        type="text"
        value={sys[key] as string}
        onChange={(e) => up({ [key]: e.target.value } as Partial<SystemSettings>)}
        className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
      {help && <p className="text-[11px] text-muted mt-1">{help}</p>}
    </div>
  );
  return { up, numField, textField };
}

export function SystemTab({
  sys,
  setSys,
  dockerAvailable,
}: {
  sys: SystemSettings;
  setSys: SetSys;
  dockerAvailable: boolean;
}) {
  const { numField } = fieldBuilders(sys, setSys);
  return (
    <div className="space-y-6">
      {/* Every field below is DB-only and read at point of use — see
          tests/test_settings_surface.py for the split this copy describes. */}
      <p className="text-[11px] text-muted">
        These values are stored in Precursor's database and set only here —
        none has a{" "}
        <code data-tooltip="Only process-level settings live in .env: bind address, database URL, data directory, ticker cadences.">
          .env
        </code>{" "}
        equivalent. Changes apply without a restart: the run timeout on the next
        scheduled run, retention on the next sweep, and the command runner on its
        next command.
      </p>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Scheduler</h3>
        {numField(
          "Scheduled run timeout (seconds)",
          "scheduled_run_timeout_seconds",
          {
            min: 10,
            max: 86_400,
            help: "How long a single scheduled automation run may take before it is cancelled. Applied live on the next run.",
          },
        )}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Storage / retention</h3>
        {numField("Tool result & thinking retention (days)", "tool_result_retention_days", {
          min: 0,
          max: 3650,
          help: "How long full tool outputs and the model's thinking are kept. After that, tool outputs are replaced with a short placeholder and the thinking is removed, to save space. 0 = keep forever.",
        })}
        <p className="text-[11px] text-muted">
          Feature-specific retention lives with its feature: agent timelines
          under <span className="font-medium">Agents</span>, workflow step inputs under{" "}
          <span className="font-medium">Workflows</span>, meeting transcripts
          under <span className="font-medium">Live</span>. Usage stats shows what
          every sweep would free, runs one on demand, and compacts the database.
        </p>
      </section>

      <CommandRunnerSection sys={sys} setSys={setSys} dockerAvailable={dockerAvailable} />
    </div>
  );
}

function CommandRunnerSection({
  sys,
  setSys,
  dockerAvailable,
}: {
  sys: SystemSettings;
  setSys: SetSys;
  dockerAvailable: boolean;
}) {
  const { up, numField, textField } = fieldBuilders(sys, setSys);
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium">Command runner (jail)</h3>
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={sys.cmd_runner_jail}
          onChange={(e) => up({ cmd_runner_jail: e.target.checked })}
          className="mt-0.5 accent-accent"
        />
        <span>
          <span className="block text-sm">Run commands in a Docker jail</span>
          <span className="block text-[11px] text-muted">
            Each command runs in a throwaway container with only its working
            directory mounted. Requires Docker.
          </span>
        </span>
      </label>

      {sys.cmd_runner_jail && !dockerAvailable && (
        <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
          Docker isn't available right now. The command-runner MCP server will
          refuse to enable until Docker is running (or jail mode is turned
          off).
        </div>
      )}

      {!sys.cmd_runner_jail && (
        <div className="text-[11px] rounded border border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400 px-3 py-2">
          <b>Jail disabled — full local disk access.</b> Commands run directly
          on the host with the backend's privileges and can read/modify any
          file the server user can. Only use this in a trusted, single-user
          environment.
        </div>
      )}

      {/* Container-only knobs — irrelevant when commands run on the host. */}
      {sys.cmd_runner_jail && (
        <>
          {textField(
            "Container image",
            "cmd_runner_image",
            "Docker image used for jailed runs. Use an image that bundles the interpreters you need (e.g. one with Node for JS).",
          )}
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={sys.cmd_runner_network}
              onChange={(e) => up({ cmd_runner_network: e.target.checked })}
              className="mt-0.5 accent-accent"
            />
            <span>
              <span className="block text-sm">Allow network access</span>
              <span className="block text-[11px] text-muted">
                When off, jailed containers run with <code>--network none</code>.
              </span>
            </span>
          </label>
        </>
      )}

      {/* These apply in both modes (host run honours timeout + output cap). */}
      {numField("Command timeout (seconds)", "cmd_runner_timeout_seconds", {
        min: 1,
        max: 3600,
      })}
      {numField("Max output bytes", "cmd_runner_max_output_bytes", {
        min: 1000,
        max: 50_000_000,
        help: "stdout/stderr are truncated past this size.",
      })}

      {sys.cmd_runner_jail && (
        <>
          {textField(
            "Memory limit",
            "cmd_runner_memory",
            "Docker --memory value, e.g. 512m or 2g.",
          )}
          {numField("PID limit", "cmd_runner_pids_limit", {
            min: 1,
            max: 100_000,
            help: "Docker --pids-limit.",
          })}
          {textField(
            "CPU limit",
            "cmd_runner_cpus",
            "Docker --cpus value, e.g. 1 or 0.5.",
          )}
        </>
      )}
    </section>
  );
}
