"""Thin async GitHub REST wrapper — only the bits Precursor needs."""

from __future__ import annotations

from typing import Any

import httpx

GITHUB_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, *, token: str, base_url: str = GITHUB_API) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _split(repo: str) -> tuple[str, str]:
        if "/" not in repo:
            raise ValueError(f"Invalid repo '{repo}', expected 'owner/name'")
        owner, name = repo.split("/", 1)
        return owner, name

    async def list_issues(
        self, repo: str, *, query: str | None = None, state: str = "open"
    ) -> list[dict[str, Any]]:
        owner, name = self._split(repo)
        if query:
            params = {
                "q": f"repo:{owner}/{name} is:issue {query}",
                "per_page": 50,
            }
            r = await self._client.get("/search/issues", params=params)
            r.raise_for_status()
            return [self._issue_summary(i) for i in r.json().get("items", [])]

        r = await self._client.get(
            f"/repos/{owner}/{name}/issues", params={"state": state, "per_page": 50}
        )
        r.raise_for_status()
        # /issues returns PRs too — filter them out.
        return [self._issue_summary(i) for i in r.json() if "pull_request" not in i]

    async def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        owner, name = self._split(repo)
        r = await self._client.get(f"/repos/{owner}/{name}/issues/{number}")
        r.raise_for_status()
        return self._issue_summary(r.json())

    async def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        owner, name = self._split(repo)
        r = await self._client.get(
            f"/repos/{owner}/{name}/issues/{number}/comments", params={"per_page": 100}
        )
        r.raise_for_status()
        return [
            {
                "id": c["id"],
                "user": c["user"]["login"],
                "body": c.get("body") or "",
                "updated_at": c["updated_at"],
            }
            for c in r.json()
        ]

    async def create_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        owner, name = self._split(repo)
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        r = await self._client.post(f"/repos/{owner}/{name}/issues", json=payload)
        r.raise_for_status()
        return self._issue_summary(r.json())

    async def list_labels(self, repo: str) -> list[dict[str, Any]]:
        owner, name = self._split(repo)
        r = await self._client.get(f"/repos/{owner}/{name}/labels", params={"per_page": 100})
        r.raise_for_status()
        return [{"name": label["name"], "color": label["color"]} for label in r.json()]

    @staticmethod
    def _issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
        return {
            "number": issue["number"],
            "title": issue["title"],
            "state": issue["state"],
            "url": issue.get("html_url"),
            "body": issue.get("body") or "",
            "labels": [
                label["name"] if isinstance(label, dict) else label
                for label in issue.get("labels", [])
            ],
            "updated_at": issue.get("updated_at"),
        }
