import { useState } from "react";
import { api } from "../../lib/api";
import { githubIdentityChanges } from "../../lib/githubAccounts";
import { modelsStore } from "../../lib/modelsStore";
import type { GitHubCliAccounts } from "../../lib/generated/apiTypes";
import type { Me } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";

const NO_IDENTITY: Me = { github: null, github_token_source: "none", proxy_user: null };

/** GitHub tab: issue associations, the reference repository, the account and the token. */
export function useGitHubSettings() {
  const [issueAssociationsEnabled, setIssueAssociationsEnabled] = useState(true);
  const [repo, setRepo] = useState("");
  const [ttlMinutes, setTtlMinutes] = useState(60);
  // Write-only: the server never returns the token, so "" keeps the saved one.
  const [githubToken, setGithubToken] = useState("");
  // The `gh` login picked here ("" = default). Sent only when it changed, since
  // a switch drops the cached token and model catalogue.
  const [cliAccount, setCliAccount] = useState("");
  const [savedCliAccount, setSavedCliAccount] = useState("");
  const [cliAccounts, setCliAccounts] = useState<GitHubCliAccounts | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const fields = [issueAssociationsEnabled, repo, ttlMinutes, cliAccount];
  const refreshIdentity = async () => {
    try {
      setMe(await api.me.get());
    } catch {
      setMe(NO_IDENTITY);
    }
  };
  const section: SettingsSection = {
    hydrate: (s) => {
      setIssueAssociationsEnabled(s.issue_associations_enabled);
      setRepo(s.github_repo);
      setTtlMinutes(s.issue_context_ttl_minutes);
      setCliAccount(s.github_cli_account);
      setSavedCliAccount(s.github_cli_account);
    },
    load: async () => {
      await refreshIdentity();
      try {
        setCliAccounts(await api.me.githubAccounts());
      } catch {
        setCliAccounts(null);
      }
    },
    payload: () => ({
      issue_associations_enabled: issueAssociationsEnabled,
      github_repo: repo,
      issue_context_ttl_minutes: ttlMinutes,
      ...(cliAccount !== savedCliAccount ? { github_cli_account: cliAccount } : {}),
      ...(githubToken ? { api_keys: { github_token: githubToken } } : {}),
    }),
    snapshot: [...fields, githubToken],
    onSaved: (updated, sent) => {
      // Another token can mean a different account/plan, hence a different
      // catalog — the composer picker must not keep the previous one, and the
      // persona must show who Precursor now acts as.
      if (sent.api_keys?.github_token || sent.github_cli_account !== undefined) {
        void modelsStore.refresh();
        void refreshIdentity();
        githubIdentityChanges.emit();
      }
      setSavedCliAccount(updated.github_cli_account);
      setGithubToken((current) => (current === githubToken ? "" : current));
      return [...fields, ""];
    },
  };
  return {
    issueAssociationsEnabled, setIssueAssociationsEnabled,
    repo, setRepo,
    ttlMinutes, setTtlMinutes,
    githubToken, setGithubToken,
    cliAccount, setCliAccount,
    cliAccounts,
    me,
    section,
  };
}

export type GitHubSettings = ReturnType<typeof useGitHubSettings>;
