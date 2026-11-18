"""Check changelog fragments, or fold them into CHANGELOG.md's [Unreleased].

Each user-facing PR adds ``changelog.d/<issue-or-slug>.<section>.md`` instead of
editing ``CHANGELOG.md``, so parallel PRs never conflict on it (ADR-0001). The
``changelog`` skill runs ``assemble`` on ``main`` from time to time.

    python3 scripts/changelog_fragments.py check
    python3 scripts/changelog_fragments.py assemble
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"
# Keep a Changelog's headings, in its order.
SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")
NAME = re.compile(rf"^[a-z0-9][a-z0-9._-]*\.({'|'.join(SECTIONS)})\.md$")
UNRELEASED = "## [Unreleased]"


def fragment_files(folder: Path = FRAGMENTS) -> list[Path]:
    return sorted(p for p in folder.glob("*.md") if p.name != "README.md")


def problems(files: list[Path]) -> list[str]:
    found = []
    for path in files:
        if not NAME.match(path.name):
            found.append(f"{path.name}: name it <issue-or-slug>.<{'|'.join(SECTIONS)}>.md")
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text.startswith("- "):
            found.append(f"{path.name}: write the entry as Markdown bullets starting with '- '")
    return found


def _section_bounds(lines: list[str]) -> tuple[int, int]:
    """Line range of the [Unreleased] body, from after its heading to the next release."""
    start = lines.index(UNRELEASED) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## [")), len(lines))
    return start, end


def _insert(body: list[str], heading: str, entry: list[str]) -> list[str]:
    """Append ``entry`` under ``### heading``, creating it in Keep a Changelog order."""
    title = f"### {heading.capitalize()}"
    if title in body:
        at = body.index(title) + 1
        while at < len(body) and not body[at].startswith("### "):
            at += 1
        while at > 0 and body[at - 1] == "":
            at -= 1
        return [*body[:at], *entry, *body[at:]]
    rank = SECTIONS.index(heading)
    later = [
        i
        for i, line in enumerate(body)
        if line.startswith("### ") and line[4:].lower() in SECTIONS[rank + 1 :]
    ]
    at = later[0] if later else len(body)
    while at > 0 and body[at - 1] == "":
        at -= 1
    return [*body[:at], "", title, "", *entry, *body[at:]]


def assemble(changelog: str, fragments: list[tuple[str, str]]) -> str:
    """``changelog`` with each ``(section, text)`` fragment added to [Unreleased]."""
    lines = changelog.split("\n")
    start, end = _section_bounds(lines)
    body = lines[start:end]
    for section, text in fragments:
        body = _insert(body, section, text.strip().split("\n"))
    while body and body[-1] == "":
        body.pop()
    return "\n".join([*lines[:start], *body, "", *lines[end:]])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("check", "assemble"))
    args = parser.parse_args()

    files = fragment_files()
    errors = problems(files)
    for error in errors:
        print(f"changelog.d/{error}", file=sys.stderr)
    if errors or args.command == "check":
        print(f"{len(files)} fragment(s), {len(errors)} problem(s)")
        return 1 if errors else 0

    pairs = [(NAME.match(p.name).group(1), p.read_text(encoding="utf-8")) for p in files]  # type: ignore[union-attr]
    CHANGELOG.write_text(assemble(CHANGELOG.read_text(encoding="utf-8"), pairs), encoding="utf-8")
    for path in files:
        path.unlink()
    print(f"assembled {len(files)} fragment(s) into {UNRELEASED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
