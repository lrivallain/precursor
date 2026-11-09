import { useState } from "react";
import { api } from "../../lib/api";
import { modelsStore } from "../../lib/modelsStore";
import type { Me } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";

/** GitHub tab: issue associations, the reference repository and the token. */
export function useGitHubSettings() {
  const [issueAssociationsEnabled, setIssueAssociationsEnabled] = useState(true);
  const [repo, setRepo] = useState("");
  const [ttlMinutes, setTtlMinutes] = useState(60);
  // Write-only: the server never returns the token, so "" keeps the saved one.
  const [githubToken, setGithubToken] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const fields = [issueAssociationsEnabled, repo, ttlMinutes];
  const section: SettingsSection = {
    hydrate: (s) => {
      setIssueAssociationsEnabled(s.issue_associations_enabled);
      setRepo(s.github_repo);
      setTtlMinutes(s.issue_context_ttl_minutes);
    },
    load: async () => {
      try {
        setMe(await api.me.get());
      } catch {
        setMe({ github: null, github_token_source: "none", proxy_user: null });
      }
    },
    payload: () => ({
      issue_associations_enabled: issueAssociationsEnabled,
      github_repo: repo,
      issue_context_ttl_minutes: ttlMinutes,
      ...(githubToken ? { api_keys: { github_token: githubToken } } : {}),
    }),
    snapshot: [...fields, githubToken],
    onSaved: (_updated, sent) => {
      // A new token can mean a different account/plan, hence a different
      // catalog — the composer picker must not keep the previous one.
      if (sent.api_keys?.github_token) void modelsStore.refresh();
      setGithubToken((current) => (current === githubToken ? "" : current));
      return [...fields, ""];
    },
  };
  return {
    issueAssociationsEnabled, setIssueAssociationsEnabled,
    repo, setRepo,
    ttlMinutes, setTtlMinutes,
    githubToken, setGithubToken,
    me,
    section,
  };
}

export type GitHubSettings = ReturnType<typeof useGitHubSettings>;
