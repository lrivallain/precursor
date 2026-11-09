import { GitHubStatusBanner } from "./GitHubStatusBanner";
import type { Settings } from "../../lib/types";
import type { GitHubSettings } from "./useGitHubSettings";

export function GitHubTab({ s, settings }: { s: GitHubSettings; settings: Settings | null }) {
  const {
    repo,
    setRepo,
    githubToken,
    setGithubToken,
    ttlMinutes,
    setTtlMinutes,
    issueAssociationsEnabled,
    setIssueAssociationsEnabled,
    me,
  } = s;
  return (
    <section>
      <GitHubStatusBanner me={me} />

      <div className="mb-4 p-3 rounded border border-border bg-surface">
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={issueAssociationsEnabled}
            onChange={(e) => setIssueAssociationsEnabled(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium">
              Enable GitHub issue associations
            </span>
            <span className="block text-[11px] text-muted mt-0.5">
              When on, topics can be linked to GitHub issues, the
              header shows issue status, and the{" "}
              <code className="font-mono">/gh-update</code>,{" "}
              <code className="font-mono">/gh-sync</code>,{" "}
              <code className="font-mono">/gh-create</code>,{" "}
              <code className="font-mono">/gh-close</code>{" "}
              slash commands are available. When off, every issue
              affordance is hidden across the app and related API
              calls are rejected. Existing topic→issue links and
              cached contexts are preserved — turn the feature back
              on to keep where you left off.
            </span>
          </span>
        </label>
      </div>

      {issueAssociationsEnabled && (
        <fieldset>
          <label className="block text-xs text-muted mb-1">
            Reference repository
          </label>
          <input
            type="text"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="owner/name"
            className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent mb-3"
          />
          <label className="block text-xs text-muted mt-3 mb-1">
            Issue context cache (minutes)
          </label>
          <input
            type="number"
            min={1}
            max={10080}
            value={ttlMinutes}
            onChange={(e) => setTtlMinutes(Number(e.target.value) || 1)}
            className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          <p className="text-[11px] text-muted mt-1">
            How long a topic's GitHub-issue summary is reused before being
            automatically refreshed. Forcing a sync from the Context tab
            always regenerates it.
          </p>
        </fieldset>
      )}

      <label className="block text-xs text-muted mt-4 mb-1">
        Personal access token{" "}
        {settings?.api_keys_present?.github_token && (
          <span className="text-green-500">(configured)</span>
        )}
      </label>
      <input
        type="password"
        value={githubToken}
        onChange={(e) => setGithubToken(e.target.value)}
        placeholder="ghp_..."
        className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
      {settings && (
        <p className="text-[11px] text-muted mt-1">
          {settings.github_token_source === "env" &&
            "Using GITHUB_TOKEN from the environment."}
          {settings.github_token_source === "gh-cli" &&
            "No token configured — using your gh CLI login. Leave blank to keep using it."}
          {settings.github_token_source === "settings" &&
            "Using token saved in Settings."}
          {settings.github_token_source === "none" &&
            "No token detected. Provide a PAT here, set GITHUB_TOKEN, or run `gh auth login`."}
        </p>
      )}
    </section>
  );
}
