"""Regenerate the JSON Schemas for agent and workflow definition files.

    uv run --frozen python scripts/gen_definition_schemas.py

The schemas are committed under ``docs/schemas/`` so editors (e.g. the VS Code
YAML extension) can validate definition files; ``tests/test_definitions.py``
fails when they drift from the Pydantic models.
"""

from __future__ import annotations

import json
from pathlib import Path

from precursor.backend.schemas.definitions import definition_json_schema

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "docs" / "schemas"


def render(kind: str) -> str:
    assert kind in ("agent", "workflow")
    return json.dumps(definition_json_schema(kind), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for kind in ("agent", "workflow"):
        target = SCHEMA_DIR / f"{kind}.schema.json"
        target.write_text(render(kind), encoding="utf-8")
        print(f"wrote {target.relative_to(SCHEMA_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
