#!/bin/bash
cd "$(dirname "$0")/.."
# Strip `gh` from PATH so the persona resolves to Guest / Not connected, and
# clear any inherited GitHub token, so no real account can leak into a shot.
#
# Resolve `uv` *first*, and invoke it by absolute path: on a Homebrew machine it
# lives in the same bin directory as `gh`, so dropping that directory would take
# the launcher with it.
UV=$(command -v uv) || { echo "uv not found on PATH" >&2; exit 1; }
PORT=${DEMO_PORT:-8899}
CLEANPATH=$(echo "$PATH" | tr ':' '\n' | while read -r d; do [ -x "$d/gh" ] || echo "$d"; done | paste -sd: -)
UNSET_TOKENS=(-u GITHUB_TOKEN -u GH_TOKEN -u GH_ENTERPRISE_TOKEN)
# DEMO_WITH_GH=1 keeps your `gh` sign-in, to try real models on the demo data.
# Never take screenshots that way: the persona shows your account.
if [ "${DEMO_WITH_GH:-}" = "1" ]; then
  CLEANPATH=$PATH
  UNSET_TOKENS=()
fi
exec env "${UNSET_TOKENS[@]}" \
  PATH="$CLEANPATH" \
  PRECURSOR_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/.demo/demo.db" \
  PRECURSOR_DATA_DIR="$(pwd)/.demo/data" \
  PRECURSOR_SKILLS_DIR="$(pwd)/.demo/skills" \
  PRECURSOR_SCHEDULER_ENABLED=false \
  PRECURSOR_PORT="$PORT" \
  "$UV" run --frozen --no-sync precursor --port "$PORT" --strict-port
