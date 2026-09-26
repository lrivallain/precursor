# Install (or re-install) Precursor from the rolling nightly build and set it up
# to run at login - the Windows twin of install.sh. Kept pure ASCII: Windows
# PowerShell 5.1 reads a BOM-less script file in the ANSI code page.
#
#   irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
#
# Like install.sh this is the "no source checkout" path: the published wheel
# already bundles the SPA, the in-app docs and every plugin frontend, so nothing
# here needs Node.js, a clone, or a build step. After it runs, `precursor
# service ...` manages the instance and `precursor service update` (or the
# tray) keeps it current.
#
# Environment overrides (the same as install.sh):
#   PRECURSOR_CHANNEL   nightly (default) | stable
#   PRECURSOR_EXTRAS    comma-separated extras (default: kanban,tray; empty = none)
#   PRECURSOR_REPO      owner/repo to install from (default: lrivallain/precursor)
#   PRECURSOR_NO_START  set to 1 to install without registering/starting it
#   PRECURSOR_WHEEL     a wheel (path or URL) to install instead of a channel

# `irm | iex` runs this in the caller's own session: everything lives in a
# script block so no variable leaks into it, and failures `throw` rather than
# `exit`, which would close the user's PowerShell window.
& {
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version 2

    function Say([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

    # Windows PowerShell 5.1 may still default to TLS 1.0, which GitHub refuses.
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $repo = if ($env:PRECURSOR_REPO) { $env:PRECURSOR_REPO } else { 'lrivallain/precursor' }
    $channel = if ($env:PRECURSOR_CHANNEL) { $env:PRECURSOR_CHANNEL } else { 'nightly' }
    # Unset means the default; set-but-empty means "install the lean core",
    # which is a different intent - as with `${PRECURSOR_EXTRAS-...}` in sh.
    $extras = if (Test-Path Env:PRECURSOR_EXTRAS) { $env:PRECURSOR_EXTRAS } else { 'kanban,tray' }

    # uv's installer puts uv.exe here but only fixes PATH for *new* terminals,
    # so a user who just installed it would otherwise be told it is missing.
    $found = Get-Command uv -ErrorAction SilentlyContinue
    $uv = if ($found) { $found.Source } else { $null }
    if (-not $uv) {
        $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (Test-Path $candidate) { $uv = $candidate }
    }
    if (-not $uv) {
        throw ("uv is required. Install it first:`n" +
            '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"' +
            "`nthen open a new terminal and run this again.")
    }

    $requirement = if ($extras) { "precursor-ai[$extras]" } else { 'precursor-ai' }
    $arguments = @('tool', 'install', '--force')

    if ($env:PRECURSOR_WHEEL) {
        $wheel = $env:PRECURSOR_WHEEL
        if (Test-Path $wheel) {
            # A PEP 508 direct reference wants a URL, not a drive-letter path.
            $wheel = ([System.Uri](Resolve-Path $wheel).Path).AbsoluteUri
        }
        Say "Installing $requirement from $wheel"
        $arguments += "$requirement @ $wheel"
    } elseif ($channel -eq 'stable') {
        Say "Installing $requirement from PyPI"
        $arguments += $requirement
    } else {
        Say "Resolving the latest nightly build of $repo"
        try {
            $manifest = Invoke-RestMethod "https://github.com/$repo/releases/download/nightly/version.json"
        } catch {
            throw "No nightly build published yet for $repo."
        }
        if (-not $manifest.wheel_url) { throw 'The nightly manifest carries no wheel URL.' }
        Say "Installing Precursor $($manifest.version)"
        $arguments += "$requirement @ $($manifest.wheel_url)"
        # Pair the host with the plugin wheels built from the same commit.
        if ($manifest.PSObject.Properties['extra_wheel_urls']) {
            foreach ($extra in @($manifest.extra_wheel_urls)) {
                if ($extra) { $arguments += @('--with', $extra) }
            }
        }
    }

    & $uv @arguments
    if ($LASTEXITCODE -ne 0) { throw "uv tool install failed (exit $LASTEXITCODE)." }

    # Called by full path: the tool directory is often not on PATH in *this*
    # session yet. `update-shell` fixes it for the next ones.
    $bin = (& $uv tool dir --bin).Trim()
    $precursor = Join-Path $bin 'precursor.exe'
    if (-not (Test-Path $precursor)) { $precursor = Join-Path $bin 'precursor-ai.exe' }
    $onPath = ($env:PATH -split ';') | Where-Object { $_.TrimEnd('\') -ieq $bin.TrimEnd('\') }
    if (-not $onPath) {
        & $uv tool update-shell | Out-Null
        Say "Added $bin to your PATH - open a new terminal to use ``precursor``."
    }

    if ($env:PRECURSOR_NO_START -eq '1') {
        Say 'Installed. Start it with: precursor service start'
        return
    }

    Say 'Registering the login item and starting Precursor'
    & $precursor service install
    if ($LASTEXITCODE -ne 0) { throw "precursor service install failed (exit $LASTEXITCODE)." }

    Write-Host @'

Precursor is installed and will start when you log in.

  precursor service status     where it is running
  precursor service update     pull the newest build and restart
  precursor service logs       tail the instance log
  precursor tray               notification-area icon (needs the `tray` extra)

'@
}
