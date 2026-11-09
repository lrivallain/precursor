#!/usr/bin/env python3
"""Quality ratchet: existing complexity debt may shrink, never grow.

The codebase already has oversized files, overly complex functions and some
copy-pasted blocks. Turning strict linters on would fail on hundreds of existing
spots, and silencing them inline would scatter noise through the code. Instead,
this script measures that debt per file and compares it to a committed baseline
(``scripts/quality_baseline.json``):

* a file that gets *worse* than its baseline, or a *new* file over a limit,
  fails the check;
* a file that gets *better* is reported, and ``--strict`` (CI) asks for the
  baseline to be tightened with ``--update``, so a fix can't be undone later.

Checks (each yields ``{key: count}``):

``file-size``    source files over ``MAX_FILE_LINES`` lines (stdlib only).
``ruff``         Python complexity (C901 / PLR0912 / PLR0915, thresholds in
                 ``pyproject.toml``), counted per file and rule.
``oxlint``       Frontend complexity, function length and hook rules
                 (``frontend/.oxlintrc.json``), counted per file and rule.
``duplication``  Total copy-pasted lines found by jscpd (``.jscpd.json``).

Usage::

    python3 scripts/quality_ratchet.py              # check, report improvements
    python3 scripts/quality_ratchet.py --strict     # CI: baseline must be tight
    python3 scripts/quality_ratchet.py --update     # record current numbers
    python3 scripts/quality_ratchet.py --only file-size

Missing tools are skipped with a note unless ``--require-tools`` is passed, so
the stdlib-only ``file-size`` check can run from a git hook on any machine.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "scripts" / "quality_baseline.json"
FRONTEND = REPO_ROOT / "frontend"

MAX_FILE_LINES = 800
SOURCE_SUFFIXES = (".py", ".ts", ".tsx")
SIZE_EXCLUDED_PARTS = ("alembic/versions/",)

RUFF_RULES = "C901,PLR0912,PLR0915"

Counts = dict[str, int]


class ToolMissingError(RuntimeError):
    """A check's external tool is not installed."""


def _rel(path: str | Path) -> str:
    resolved = Path(path)
    if not resolved.is_absolute():
        return resolved.as_posix()
    return resolved.relative_to(REPO_ROOT).as_posix()


def _tool(env_var: str, *candidates: Path | str) -> str:
    """Resolve a tool from an env override, a local install, or PATH."""
    override = os.environ.get(env_var)
    if override:
        return override
    for candidate in candidates:
        if isinstance(candidate, Path):
            if candidate.exists():
                return str(candidate)
        elif found := shutil.which(candidate):
            return found
    raise ToolMissingError(f"set {env_var} or install it")


def _run_json(cmd: list[str], cwd: Path) -> object:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{cmd[0]} produced no JSON (exit {proc.returncode}):\n{proc.stderr[-2000:]}"
        ) from exc


# --- checks -----------------------------------------------------------------


def _source_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        p
        for p in out.splitlines()
        if p.endswith(SOURCE_SUFFIXES)
        and not any(part in p for part in SIZE_EXCLUDED_PARTS)
        and (REPO_ROOT / p).is_file()
    ]


def check_file_size() -> Counts:
    counts: Counts = {}
    for path in _source_files():
        with (REPO_ROOT / path).open("rb") as fh:
            lines = sum(1 for _ in fh)
        if lines > MAX_FILE_LINES:
            counts[path] = lines
    return counts


def check_ruff() -> Counts:
    ruff = _tool("RUFF", "ruff")
    cmd = [ruff, "check", ".", "--select", RUFF_RULES, "--output-format", "json", "--exit-zero"]
    diagnostics = _run_json(cmd, REPO_ROOT)
    assert isinstance(diagnostics, list)
    return dict(Counter(f"{_rel(d['filename'])}::{d['code']}" for d in diagnostics))


def check_oxlint() -> Counts:
    oxlint = _tool("OXLINT", FRONTEND / "node_modules" / ".bin" / "oxlint", "oxlint")
    cmd = [oxlint, "-c", ".oxlintrc.json", "--format", "json", "src"]
    report = _run_json(cmd, FRONTEND)
    assert isinstance(report, dict)
    return dict(
        Counter(
            f"frontend/{d['filename']}::{d['code']}"
            for d in report["diagnostics"]
            if d.get("severity") == "error"
        )
    )


def check_duplication() -> Counts:
    jscpd = _tool("JSCPD", FRONTEND / "node_modules" / ".bin" / "jscpd", "jscpd")
    with tempfile.TemporaryDirectory() as out_dir:
        subprocess.run(
            [jscpd, "--config", ".jscpd.json", "--output", out_dir],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        report_path = Path(out_dir) / "jscpd-report.json"
        if not report_path.exists():
            raise RuntimeError("jscpd wrote no report")
        report = json.loads(report_path.read_text())
    return {"duplicated-lines": int(report["statistics"]["total"]["duplicatedLines"])}


CHECKS: dict[str, Callable[[], Counts]] = {
    "file-size": check_file_size,
    "ruff": check_ruff,
    "oxlint": check_oxlint,
    "duplication": check_duplication,
}


# --- comparison -------------------------------------------------------------


def compare(baseline: Counts, current: Counts) -> tuple[list[str], list[str]]:
    """Return (regressions, improvements) as human-readable lines."""
    regressions = [
        f"{key}: {baseline.get(key, 0)} -> {value}"
        for key, value in sorted(current.items())
        if value > baseline.get(key, 0)
    ]
    improvements = [
        f"{key}: {value} -> {current.get(key, 0)}"
        for key, value in sorted(baseline.items())
        if current.get(key, 0) < value
    ]
    return regressions, improvements


def load_baseline() -> dict[str, Counts]:
    if not BASELINE_PATH.exists():
        return {}
    data: dict[str, Counts] = json.loads(BASELINE_PATH.read_text())
    return data


def write_baseline(data: dict[str, Counts]) -> None:
    ordered = {name: dict(sorted(data[name].items())) for name in CHECKS if name in data}
    BASELINE_PATH.write_text(json.dumps(ordered, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", choices=list(CHECKS), help="run these checks")
    parser.add_argument("--update", action="store_true", help="record current numbers")
    parser.add_argument("--strict", action="store_true", help="fail on an untightened baseline")
    parser.add_argument("--require-tools", action="store_true", help="fail if a tool is missing")
    args = parser.parse_args(argv)

    baseline = load_baseline()
    failed = False
    for name in args.only or list(CHECKS):
        try:
            current = CHECKS[name]()
        except ToolMissingError as exc:
            print(f"[{name}] skipped: tool not found ({exc})")
            failed |= args.require_tools
            continue

        if args.update:
            baseline[name] = current
            print(f"[{name}] baseline recorded ({sum(current.values())} total)")
            continue

        regressions, improvements = compare(baseline.get(name, {}), current)
        for line in regressions:
            print(f"[{name}] WORSE  {line}")
        for line in improvements:
            print(f"[{name}] better {line}")
        if regressions:
            failed = True
        if improvements and args.strict:
            failed = True
        if not regressions and not improvements:
            print(f"[{name}] ok")

    if args.update:
        write_baseline(baseline)
        return 0
    if failed:
        print(
            "\nQuality ratchet failed. Split or simplify the code flagged WORSE "
            "(see CONTRIBUTING.md > Quality ratchet). If you improved things, "
            "record it with: make ratchet-update",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
