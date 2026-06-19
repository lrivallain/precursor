"""Thin async GitHub REST wrapper — only the bits Precursor needs."""

from __future__ import annotations

from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"


class GitHubClient:
    def __init__(self, *, token: str, base_url: str = GITHUB_API) -> None:
        self._token = token
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
            params: dict[str, str | int] = {
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

    async def update_issue(
        self,
        repo: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        state_reason: str | None = None,
    ) -> dict[str, Any]:
        owner, name = self._split(repo)
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if state_reason is not None:
            payload["state_reason"] = state_reason
        r = await self._client.patch(f"/repos/{owner}/{name}/issues/{number}", json=payload)
        r.raise_for_status()
        return self._issue_summary(r.json())

    async def list_labels(self, repo: str) -> list[dict[str, Any]]:
        owner, name = self._split(repo)
        r = await self._client.get(f"/repos/{owner}/{name}/labels", params={"per_page": 100})
        r.raise_for_status()
        return [{"name": label["name"], "color": label["color"]} for label in r.json()]

    async def add_issue_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        owner, name = self._split(repo)
        r = await self._client.post(
            f"/repos/{owner}/{name}/issues/{number}/comments",
            json={"body": body},
        )
        r.raise_for_status()
        c = r.json()
        return {
            "id": c["id"],
            "url": c.get("html_url"),
            "body": c.get("body") or "",
        }

    async def upload_issue_comment_attachment(
        self,
        repo: str,
        number: int,
        *,
        filename: str,
        content: bytes,
        mime: str,
    ) -> str:
        owner, name = self._split(repo)
        safe_name = filename.strip() or "image"
        files = {
            "file": (safe_name, content, mime),
        }
        data = {
            "name": safe_name,
            "size": str(len(content)),
            "content_type": mime,
        }
        async with httpx.AsyncClient(
            base_url=GITHUB_UPLOADS,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        ) as upload_client:
            r = await upload_client.post(
                f"/repos/{owner}/{name}/issues/{number}/comments/assets",
                data=data,
                files=files,
            )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            raise httpx.HTTPStatusError(
                f"{exc}. Response body: {detail or '(empty)'}",
                request=exc.request,
                response=exc.response,
            ) from exc
        payload = r.json()
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("GitHub image upload did not return a URL")
        return url

    async def get_authenticated_user(self) -> dict[str, Any]:
        r = await self._client.get("/user")
        r.raise_for_status()
        u = r.json()
        return {
            "login": u.get("login") or "",
            "name": u.get("name"),
            "avatar_url": u.get("avatar_url"),
            "html_url": u.get("html_url"),
        }

    @staticmethod
    def _issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
        return {
            "number": issue["number"],
            "title": issue["title"],
            "state": issue["state"],
            "url": issue.get("html_url"),
            "body": issue.get("body") or "",
            "labels": [
                {"name": label["name"], "color": label.get("color") or "888888"}
                if isinstance(label, dict)
                else {"name": label, "color": "888888"}
                for label in issue.get("labels", [])
            ],
            "updated_at": issue.get("updated_at"),
        }
