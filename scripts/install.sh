#!/bin/sh
# Install (or re-install) Precursor from the rolling nightly build and set it up
# to run at login.
#
#   curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
#
# This is the "no source checkout" path: the published wheel already bundles the
# SPA, the in-app docs and every plugin frontend, so nothing here needs Node.js,
# a clone, or a build step. After it runs, `precursor service …` manages the
# instance and `precursor service update` (or the tray) keeps it current.
#
# Environment overrides:
#   PRECURSOR_CHANNEL   nightly (default) | stable
#   PRECURSOR_EXTRAS    comma-separated extras (default: kanban,tray)
#   PRECURSOR_REPO      owner/repo to install from (default: lrivallain/precursor)
#   PRECURSOR_NO_START  set to 1 to install without registering/starting it

set -eu

REPO="${PRECURSOR_REPO:-lrivallain/precursor}"
CHANNEL="${PRECURSOR_CHANNEL:-nightly}"
EXTRAS="${PRECURSOR_EXTRAS:-kanban,tray}"

say() { printf '\033[1m==>\033[0m %s\n' "$1"; }
die() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || die \
  "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
command -v curl >/dev/null 2>&1 || die "curl is required."

REQUIREMENT="precursor-ai[${EXTRAS}]"

if [ "$CHANNEL" = "stable" ]; then
  say "Installing ${REQUIREMENT} from PyPI"
  uv tool install --force "$REQUIREMENT"
else
  say "Resolving the latest nightly build of ${REPO}"
  MANIFEST=$(curl -fsSL "https://github.com/${REPO}/releases/download/nightly/version.json") \
    || die "No nightly build published yet for ${REPO}."

  # Keep the dependency surface at "things every POSIX box has": python3 ships
  # with uv's own prerequisites anyway, and parsing JSON with sed is a trap.
  read_field() {
    printf '%s' "$MANIFEST" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))"
  }
  read_extras() {
    printf '%s' "$MANIFEST" | python3 -c \
      "import json,sys; print('\n'.join(json.load(sys.stdin).get('extra_wheel_urls') or []))"
  }

  VERSION=$(read_field version)
  WHEEL=$(read_field wheel_url)
  [ -n "$WHEEL" ] || die "The nightly manifest carries no wheel URL."

  say "Installing Precursor ${VERSION}"
  set -- uv tool install --force "${REQUIREMENT} @ ${WHEEL}"
  # Pair the host with the plugin wheels built from the same commit.
  for extra in $(read_extras); do
    set -- "$@" --with "$extra"
  done
  "$@"
fi

if [ "${PRECURSOR_NO_START:-0}" = "1" ]; then
  say "Installed. Start it with: precursor service start"
  exit 0
fi

say "Registering the login item and starting Precursor"
precursor service install

cat <<'EOF'

Precursor is installed and will start when you log in.

  precursor service status     where it is running
  precursor service update     pull the newest build and restart
  precursor service logs       tail the instance log
  precursor tray               menu-bar control (needs the `tray` extra)

EOF
