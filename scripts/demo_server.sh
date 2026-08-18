#!/bin/bash
cd "$(dirname "$0")/.."
# Strip `gh` from PATH so the persona resolves to Guest / Not connected, and
# clear any inherited GitHub token, so no real account can leak into a shot.
CLEANPATH=$(echo "$PATH" | tr ':' '\n' | while read -r d; do [ -x "$d/gh" ] || echo "$d"; done | paste -sd: -)
exec env -u GITHUB_TOKEN -u GH_TOKEN -u GH_ENTERPRISE_TOKEN \
  PATH="$CLEANPATH" \
  PRECURSOR_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/.demo/demo.db" \
  PRECURSOR_DATA_DIR="$(pwd)/.demo/data" \
  PRECURSOR_SKILLS_DIR="$(pwd)/.demo/skills" \
  PRECURSOR_SCHEDULER_ENABLED=false \
  PRECURSOR_PORT=8899 \
  uv run precursor --port 8899 --strict-port
