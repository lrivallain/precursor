import { Select } from "../Select";
import { accountOptions, showAccountPicker } from "../../lib/githubAccounts";
import type { GitHubCliAccounts } from "../../lib/generated/apiTypes";

interface Props {
  value: string;
  onChange: (login: string) => void;
  accounts: GitHubCliAccounts | null;
  /** A saved personal access token takes precedence over any `gh` login. */
  patSaved: boolean;
}

/** Which `gh` login Precursor acts as, when several are signed in. */
export function GitHubAccountPicker({ value, onChange, accounts, patSaved }: Props) {
  if (!accounts || !showAccountPicker(accounts)) return null;
  return (
    <div className="mb-4">
      <label className="block text-xs text-muted mb-1">GitHub account</label>
      <Select
        value={value}
        onChange={onChange}
        options={accountOptions(accounts)}
        ariaLabel="GitHub account"
        fullWidth
      />
      <p className="text-[11px] text-muted mt-1">
        {patSaved
          ? "Not in use: the personal access token saved below takes precedence. Clear it to use a gh account."
          : "Several accounts are signed in to the gh CLI. The one picked here signs Precursor's GitHub calls, the persona and the GitHub Copilot models (agents mode keeps the Copilot CLI's own sign-in)."}
      </p>
    </div>
  );
}
