"""``precursor validate [FOLDER]`` — check definition files without the app.

Meant for a definitions repository's CI as much as for a terminal. It needs no
database, so it checks structure and cross-file references only; whether a
role or MCP server name exists is a property of an instance, which the running
app's check reports (``GET /api/definitions/check``).

Exit status: 0 when there are no errors, 1 when there are, 2 when the folder
doesn't exist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from precursor.backend.schemas.definitions_api import DefinitionIssue
from precursor.backend.services.definitions.checker import build_report
from precursor.backend.services.definitions.loader import load_definitions


def _format(issue: DefinitionIssue) -> str:
    where = ": ".join(part for part in (issue.path, issue.location) if part)
    return (
        f"{issue.severity}: {where}: {issue.message}"
        if where
        else (f"{issue.severity}: {issue.message}")
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="precursor validate",
        description="Check a folder of *.agent.yaml / *.workflow.yaml definition files.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help="Folder to check (default: PRECURSOR_DEFINITIONS_DIR, else <data dir>/definitions).",
    )
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    args = parser.parse_args(argv)

    if args.folder:
        root = Path(args.folder).expanduser().resolve()
    else:
        from precursor.backend.config import get_settings

        root = Path(get_settings().definitions_dir)
    if not root.is_dir():
        print(f"precursor validate: {root} is not a folder", file=sys.stderr)
        return 2

    report = build_report(load_definitions(root))
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        for issue in report.issues:
            print(_format(issue))
        print(
            f"Checked {_plural(len(report.files), 'file')} in {root}: "
            f"{_plural(report.error_count, 'error')}, {_plural(report.warning_count, 'warning')}."
        )
    return 0 if report.ok else 1
