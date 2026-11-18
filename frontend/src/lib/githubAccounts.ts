import { Emitter } from "./externalStore";
import type { GitHubCliAccounts } from "./generated/apiTypes";

/**
 * Fires when the GitHub identity Precursor acts as may have changed (another
 * `gh` account picked, a token saved), so the persona re-reads `/api/me`.
 */
export const githubIdentityChanges = new Emitter();

export interface AccountOption {
  value: string;
  label: string;
}

/** Whether the Settings picker is worth showing: several logins, or a stale pick to clear. */
export function showAccountPicker(list: GitHubCliAccounts | null): boolean {
  if (!list) return false;
  return list.accounts.length > 1 || (list.selected !== "" && list.accounts.length > 0);
}

/** Picker options: "" follows the env pin, else the account `gh` has active. */
export function accountOptions(list: GitHubCliAccounts): AccountOption[] {
  const active = list.accounts.find((a) => a.active)?.login;
  const fallback = list.env_pinned || active;
  const options: AccountOption[] = [
    {
      value: "",
      label: fallback
        ? `Default (@${fallback}${list.env_pinned ? ", from PRECURSOR_GITHUB_CLI_USER" : ", active in gh"})`
        : "Default",
    },
    ...list.accounts.map((a) => ({ value: a.login, label: `@${a.login}` })),
  ];
  // A pick whose login has since been signed out stays visible so it can be cleared.
  if (list.selected && !list.accounts.some((a) => a.login === list.selected)) {
    options.push({ value: list.selected, label: `@${list.selected} (not signed in to gh)` });
  }
  return options;
}
