import type { Me } from "../../lib/types";

export function GitHubStatusBanner({ me }: { me: Me | null }) {
  if (me === null) {
    return (
      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-border bg-surface text-xs text-muted">
        <span className="w-2 h-2 rounded-full bg-muted/60" />
        Checking GitHub connectivity…
      </div>
    );
  }
  // Identity resolved → token works and the GitHub Copilot provider can
  // call the API on the user's behalf.
  if (me.github) {
    return (
      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-green-500/30 bg-green-500/10 text-xs">
        <span className="w-2 h-2 rounded-full bg-green-500" />
        <span className="text-text">
          Connected as{" "}
          <span className="font-medium">@{me.github.login}</span> — GitHub
          serves as the auth and models provider.
        </span>
      </div>
    );
  }
  // A token is configured but the API didn't return an identity. Usually
  // an expired/revoked token, network error, or insufficient scopes.
  if (me.github_token_source !== "none") {
    return (
      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-amber-500/30 bg-amber-500/10 text-xs text-text">
        <span className="w-2 h-2 rounded-full bg-amber-500" />
        <span>
          Token detected ({me.github_token_source}) but identity could not be
          resolved. Verify the token is valid and has access to the GitHub
          Models API.
        </span>
      </div>
    );
  }
  return (
    <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded border border-border bg-surface text-xs text-muted">
      <span className="w-2 h-2 rounded-full bg-muted/60" />
      Not connected — set a personal access token below, export
      <code className="mx-1 font-mono">GITHUB_TOKEN</code>, or run
      <code className="ml-1 font-mono">gh auth login</code>.
    </div>
  );
}
