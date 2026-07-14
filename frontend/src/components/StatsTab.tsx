import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import type {
  DatabaseStats,
  IssueStats,
  SystemStats,
  UsageBucket,
  UsageStats,
} from "../lib/types";

type Granularity = "weekly" | "monthly" | "yearly";

const GRANULARITIES: ReadonlyArray<{ id: Granularity; label: string }> = [
  { id: "weekly", label: "Week" },
  { id: "monthly", label: "Month" },
  { id: "yearly", label: "Year" },
];

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || value < 10 ? 1 : 2)} ${units[unit]}`;
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatPeriod(period: string): string {
  const week = period.match(/^(\d{4})-W(\d{2})$/);
  if (week) return `${week[1]} · week ${Number(week[2])}`;
  const month = period.match(/^(\d{4})-(\d{2})$/);
  if (month) {
    const date = new Date(Number(month[1]), Number(month[2]) - 1, 1);
    return date.toLocaleDateString(undefined, { year: "numeric", month: "short" });
  }
  return period;
}

function shortPeriod(period: string): string {
  const week = period.match(/^\d{4}-W(\d{2})$/);
  if (week) return `W${Number(week[1])}`;
  const month = period.match(/^(\d{4})-(\d{2})$/);
  if (month) {
    const date = new Date(Number(month[1]), Number(month[2]) - 1, 1);
    return date.toLocaleDateString(undefined, { month: "short" });
  }
  return period;
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex-1 min-w-[120px] rounded border border-border bg-surface px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{formatNumber(value)}</div>
    </div>
  );
}

function TextStatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="flex-1 min-w-[120px] rounded border border-border bg-surface px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function IssuesSection({ issues }: { issues: IssueStats }) {
  if (!issues.configured) {
    return (
      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
          GitHub issues
        </h4>
        <p className="text-[11px] text-muted">
          No GitHub repository configured. Set one in Settings → GitHub to see
          open vs. closed issue counts.
        </p>
      </section>
    );
  }
  const total =
    issues.open != null && issues.closed != null ? issues.open + issues.closed : null;
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
        GitHub issues{issues.repo ? ` · ${issues.repo}` : ""}
      </h4>
      {issues.error ? (
        <p className="text-[11px] text-red-500">
          Couldn't load issue counts: {issues.error}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <StatCard label="Open" value={issues.open ?? 0} />
          <StatCard label="Closed" value={issues.closed ?? 0} />
          {total != null && <StatCard label="Total" value={total} />}
        </div>
      )}
    </section>
  );
}

function DatabaseSection({ database }: { database: DatabaseStats }) {
  const [open, setOpen] = useState(false);
  // Largest tables first so the heaviest rows surface at the top.
  const tables = useMemo(
    () =>
      [...database.tables].sort(
        (a, b) => (b.size_bytes ?? 0) - (a.size_bytes ?? 0) || b.row_count - a.row_count,
      ),
    [database.tables],
  );
  const hasSizes = tables.some((t) => t.size_bytes != null);
  const totalRows = tables.reduce((n, t) => n + t.row_count, 0);
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
        Database
      </h4>
      <div className="flex flex-wrap gap-2">
        <TextStatCard
          label="On-disk size"
          value={formatBytes(database.size_bytes)}
          sub={database.engine}
        />
        <StatCard label="Tables" value={tables.length} />
        <StatCard label="Total rows" value={totalRows} />
      </div>
      <div className="rounded border border-border bg-surface overflow-hidden">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold text-muted hover:text-text"
        >
          <span className="text-muted">
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
          <span className="flex-1 text-left uppercase tracking-wide">
            Per-table breakdown
          </span>
          <span className="tabular-nums font-normal normal-case tracking-normal">
            {tables.length} tables
          </span>
        </button>
        {open && (
          <>
            <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold text-muted border-t border-border">
              <div className="flex-1">Table</div>
              <div className="w-20 text-right">Rows</div>
              {hasSizes && <div className="w-20 text-right">Size</div>}
            </div>
            <div className="max-h-64 overflow-y-auto">
              {tables.map((t) => (
                <div
                  key={t.name}
                  className="flex items-center gap-2 px-3 py-1 text-xs border-t border-border/50"
                >
                  <div className="flex-1 truncate font-mono text-[11px]" title={t.name}>
                    {t.name}
                  </div>
                  <div className="w-20 text-right tabular-nums">
                    {formatNumber(t.row_count)}
                  </div>
                  {hasSizes && (
                    <div className="w-20 text-right tabular-nums text-muted">
                      {formatBytes(t.size_bytes)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function SystemStatsView({ system }: { system: SystemStats }) {
  const { entities, blobs, database, issues } = system;
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
          Content
        </h4>
        <div className="flex flex-wrap gap-2">
          <StatCard label="Topics" value={entities.topics} />
          <StatCard label="Chats" value={entities.chats} />
          <StatCard label="Agents" value={entities.agents} />
          <StatCard label="Workspaces" value={entities.workspaces} />
        </div>
      </section>

      <DatabaseSection database={database} />

      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
          Attachment blobs
        </h4>
        <div className="flex flex-wrap gap-2">
          <TextStatCard label="Total size" value={formatBytes(blobs.size_bytes)} />
          <StatCard label="Files" value={blobs.count} />
        </div>
      </section>

      <IssuesSection issues={issues} />
    </div>
  );
}

function UsageChart({ buckets }: { buckets: UsageBucket[] }) {
  // Oldest -> newest, left -> right.
  const data = buckets;
  const max = data.reduce((m, b) => Math.max(m, b.total_tokens), 0);

  if (data.length === 0) {
    return <p className="text-[11px] text-muted">No usage recorded yet.</p>;
  }

  const width = 520;
  const height = 180;
  const padX = 8;
  const padBottom = 22;
  const padTop = 18;
  const plotW = width - padX * 2;
  const plotH = height - padBottom - padTop;
  const slot = plotW / data.length;
  const barW = Math.max(2, Math.min(40, slot * 0.6));
  // Only label every Nth tick so a dense weekly series stays readable.
  const labelStep = Math.ceil(data.length / 12);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-auto"
      role="img"
      aria-label="Token usage over time"
    >
      <line
        x1={padX}
        y1={padTop + plotH}
        x2={width - padX}
        y2={padTop + plotH}
        style={{ stroke: "var(--border)" }}
        strokeWidth={1}
      />
      {data.map((b, i) => {
        const cx = padX + slot * i + slot / 2;
        const x = cx - barW / 2;
        const promptH = max > 0 ? (b.prompt_tokens / max) * plotH : 0;
        const completionH = max > 0 ? (b.completion_tokens / max) * plotH : 0;
        const promptY = padTop + plotH - promptH;
        const completionY = promptY - completionH;
        return (
          <g key={b.period}>
            <title>
              {`${formatPeriod(b.period)}\nPrompt: ${formatNumber(b.prompt_tokens)}\nCompletion: ${formatNumber(b.completion_tokens)}\nTotal: ${formatNumber(b.total_tokens)}`}
            </title>
            <rect
              x={x}
              y={promptY}
              width={barW}
              height={promptH}
              style={{ fill: "var(--accent)", fillOpacity: 0.85 }}
            />
            <rect
              x={x}
              y={completionY}
              width={barW}
              height={completionH}
              style={{ fill: "var(--accent)", fillOpacity: 0.4 }}
            />
            {b.total_tokens > 0 && i % labelStep === 0 && (
              <text
                x={cx}
                y={Math.max(padTop + 8, completionY - 4)}
                textAnchor="middle"
                fontSize={9}
                fontWeight={600}
                style={{ fill: "var(--text)" }}
              >
                {formatNumber(b.total_tokens)}
              </text>
            )}
            {i % labelStep === 0 && (
              <text
                x={cx}
                y={height - 6}
                textAnchor="middle"
                fontSize={9}
                style={{ fill: "var(--muted)" }}
              >
                {shortPeriod(b.period)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function StatsTab() {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [system, setSystem] = useState<SystemStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [granularity, setGranularity] = useState<Granularity>("monthly");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [usage, sys] = await Promise.all([
          api.system.getUsageStats(),
          api.system.getSystemStats(),
        ]);
        if (!cancelled) {
          setStats(usage);
          setSystem(sys);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const series = useMemo<UsageBucket[]>(() => {
    if (!stats) return [];
    return stats[granularity];
  }, [stats, granularity]);

  if (loading) {
    return <p className="text-xs text-muted">Loading usage statistics…</p>;
  }

  if (error) {
    return (
      <div className="text-xs text-red-500 border border-red-500/30 rounded p-2">
        {error}
      </div>
    );
  }

  if (!stats) return null;

  const { totals } = stats;
  // Most recent buckets first for the table beneath the chart.
  const rows = [...series].reverse();

  return (
    <div className="space-y-8">
      {system && <SystemStatsView system={system} />}

      <div className="space-y-6">
        <div className="space-y-1">
          <h3 className="text-sm font-medium">AI consumption</h3>
          <p className="text-[11px] text-muted">
            Combined token usage reported by the model across every topic and
            chat. Turns that don't report usage are not counted.
          </p>
        </div>

      <section className="space-y-2">
        <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
          Totals
        </h4>
        <div className="flex flex-wrap gap-2">
          <StatCard label="Total tokens" value={totals.total_tokens} />
          <StatCard label="Prompt tokens" value={totals.prompt_tokens} />
          <StatCard label="Completion tokens" value={totals.completion_tokens} />
          <StatCard label="Messages" value={totals.message_count} />
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-xs font-semibold text-muted uppercase tracking-wide">
            Over time
          </h4>
          <div className="flex rounded border border-border overflow-hidden text-xs">
            {GRANULARITIES.map((g) => {
              const active = granularity === g.id;
              return (
                <button
                  key={g.id}
                  type="button"
                  onClick={() => setGranularity(g.id)}
                  className={`px-2.5 py-1 ${
                    active
                      ? "bg-accent text-white"
                      : "bg-surface text-muted hover:text-accent"
                  }`}
                >
                  {g.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="rounded border border-border bg-surface p-3">
          <UsageChart buckets={series} />
          <div className="mt-2 flex items-center gap-4 text-[11px] text-muted">
            <span className="flex items-center gap-1">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ background: "var(--accent)", opacity: 0.85 }}
              />
              Prompt
            </span>
            <span className="flex items-center gap-1">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ background: "var(--accent)", opacity: 0.4 }}
              />
              Completion
            </span>
          </div>
        </div>

        {rows.length > 0 && (
          <div className="space-y-1">
            {rows.map((b) => (
              <div key={b.period} className="flex items-center gap-2 text-xs">
                <div className="w-28 shrink-0 text-muted">
                  {formatPeriod(b.period)}
                </div>
                <div className="flex-1 text-right tabular-nums" title="Total tokens">
                  {formatNumber(b.total_tokens)}
                </div>
                <div
                  className="w-20 shrink-0 text-right tabular-nums text-muted"
                  title="Messages"
                >
                  {compact(b.message_count)} msg
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      </div>
    </div>
  );
}
