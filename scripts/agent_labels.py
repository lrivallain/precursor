"""Create or update the repository labels listed in .github/labels.json."""

from __future__ import annotations

import json
from pathlib import Path

import agent_github as github

LABELS = Path(__file__).resolve().parent.parent / ".github" / "labels.json"


def main() -> None:
    for label in json.loads(LABELS.read_text(encoding="utf-8")):
        github.gh(
            "label",
            "create",
            label["name"],
            "--color",
            label["color"],
            "--description",
            label["description"],
            "--force",
        )
        print(f"synced {label['name']}")


if __name__ == "__main__":
    main()
