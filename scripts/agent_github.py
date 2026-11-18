"""GitHub helpers shared by the agent workflows (stdlib only, runs on bare runners)."""

from __future__ import annotations

import json
import subprocess
from typing import Any
from urllib.parse import quote

BOT_LOGIN = "github-actions[bot]"


def marker(key: str) -> str:
    """The hidden tag that marks the one comment a workflow owns on a thread."""
    return f"<!-- precursor-agent:{key} -->"


def gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True)
    return result.stdout


def gh_api(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    args = ["api", "-X", method, path]
    if body is not None:
        args += ["--input", "-"]
    out = gh(*args, stdin=json.dumps(body) if body is not None else None)
    return json.loads(out) if out.strip() else None


def gh_paginated(path: str) -> list[Any]:
    pages = json.loads(gh("api", "--paginate", "--slurp", path) or "[]")
    return [item for page in pages for item in page]


def upsert_comment(repo: str, number: int, key: str, body: str | None) -> None:
    """Create or update the comment this workflow owns; ``None`` deletes it."""
    tag = marker(key)
    mine = [
        c
        for c in gh_paginated(f"repos/{repo}/issues/{number}/comments")
        if c.get("user", {}).get("login") == BOT_LOGIN and tag in (c.get("body") or "")
    ]
    if body is None:
        for c in mine:
            gh_api(f"repos/{repo}/issues/comments/{c['id']}", "DELETE")
        return
    payload = {"body": f"{tag}\n{body}"}
    if mine:
        gh_api(f"repos/{repo}/issues/comments/{mine[0]['id']}", "PATCH", payload)
    else:
        gh_api(f"repos/{repo}/issues/{number}/comments", "POST", payload)


def set_labels(repo: str, number: int, add: list[str], remove: list[str]) -> None:
    current = {label["name"] for label in gh_api(f"repos/{repo}/issues/{number}/labels")}
    for name in remove:
        if name in current:
            gh_api(f"repos/{repo}/issues/{number}/labels/{quote(name, safe='')}", "DELETE")
    missing = [name for name in add if name not in current]
    if missing:
        gh_api(f"repos/{repo}/issues/{number}/labels", "POST", {"labels": missing})
