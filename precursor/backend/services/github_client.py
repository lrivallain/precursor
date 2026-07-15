"""Thin async GitHub REST wrapper — only the bits Precursor needs."""

from __future__ import annotations

from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"

# GitHub Projects v2 returns colour names for single-select field options.
# Map the named colours to reasonable hex values so the front-end can use them
# without a separate lookup table.
_PROJECT_COLORS: dict[str, str] = {
    "GREEN": "#2da44e",
    "YELLOW": "#d29922",
    "ORANGE": "#e16f24",
    "RED": "#cf222e",
    "PINK": "#bf4b8a",
    "PURPLE": "#8250df",
    "BLUE": "#0969da",
    "GRAY": "#6e7781",
    "GREY": "#6e7781",
}


def _project_color(name: str) -> str:
    """Return a hex colour for a GitHub project option colour name."""
    return _PROJECT_COLORS.get(name.upper(), "#6e7781")


class GitHubRepoNotAccessibleError(Exception):
    """The repository can't be resolved for the current token.

    Raised when GitHub reports the repo as nonexistent or invisible (e.g. a
    private repo the token can't see). Lets callers degrade to a friendly
    message instead of surfacing a raw API error.
    """

    def __init__(self, repo: str) -> None:
        super().__init__(f"Repository '{repo}' not found or not accessible")
        self.repo = repo


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

    async def count_issues_by_state(self, repo: str) -> tuple[int, int]:
        """Return ``(open, closed)`` issue counts via the GraphQL API.

        GraphQL yields exact counts in a single request and, unlike the REST
        ``/search/issues`` endpoint, distinguishes an inaccessible or
        nonexistent repo (a ``NOT_FOUND`` error with ``repository: null``, HTTP
        200) from a valid but empty repo. The search endpoint instead returns a
        raw 422 for an unresolvable ``repo:`` qualifier, so switching to GraphQL
        lets us degrade cleanly and avoids the search API's migration quirks.
        """
        owner, name = self._split(repo)
        query = (
            "query($o:String!,$n:String!){"
            "repository(owner:$o,name:$n){"
            "open:issues(states:OPEN){totalCount}"
            "closed:issues(states:CLOSED){totalCount}"
            "}}"
        )
        r = await self._client.post(
            "/graphql",
            json={"query": query, "variables": {"o": owner, "n": name}},
        )
        r.raise_for_status()
        payload = r.json()
        repository = (payload.get("data") or {}).get("repository")
        if repository is None:
            # null repository ⇒ NOT_FOUND / no permission for this token.
            raise GitHubRepoNotAccessibleError(repo)
        return (
            int(repository["open"]["totalCount"]),
            int(repository["closed"]["totalCount"]),
        )

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

    async def list_projects(self, repo: str) -> list[dict[str, Any]]:
        """Return Projects v2 linked to *repo* (owner/name)."""
        owner, name = self._split(repo)
        query = (
            "query($o:String!,$n:String!){"
            "repository(owner:$o,name:$n){"
            "projectsV2(first:20){"
            "nodes{id number title url}"
            "}}}"
        )
        r = await self._client.post(
            "/graphql",
            json={"query": query, "variables": {"o": owner, "n": name}},
        )
        r.raise_for_status()
        payload = r.json()
        repository = (payload.get("data") or {}).get("repository")
        if repository is None:
            raise GitHubRepoNotAccessibleError(repo)
        return [
            {
                "id": node["id"],
                "number": node["number"],
                "title": node["title"],
                "url": node.get("url") or "",
            }
            for node in (repository.get("projectsV2") or {}).get("nodes") or []
        ]

    async def get_project_board(self, project_node_id: str) -> dict[str, Any]:
        """Return columns + issues for a Projects v2 node.

        Fetches the first single-select field named "Status" (case-insensitive)
        as the column source, plus all project items that are issues.  Items
        with no status value land in ``column_id: null`` (an implicit "No
        status" column rendered by the front-end).
        """
        # First pass: fetch field definitions and items in one query.
        query = (
            "query($id:ID!){"
            "node(id:$id){"
            "...on ProjectV2{"
            "fields(first:20){"
            "nodes{"
            "...on ProjectV2SingleSelectField{"
            "id name"
            " options{id name color}"
            "}"
            "}"
            "}"
            "items(first:100){"
            "nodes{"
            "id"
            " fieldValueByName(name:\"Status\"){"
            "...on ProjectV2ItemFieldSingleSelectValue{optionId}"
            "}"
            " content{"
            "...on Issue{"
            "number title state url body"
            " labels(first:10){nodes{name color}}"
            " updatedAt"
            "}"
            "}"
            "}"
            "}"
            "}"
            "}}"
        )
        r = await self._client.post(
            "/graphql",
            json={"query": query, "variables": {"id": project_node_id}},
        )
        r.raise_for_status()
        payload = r.json()
        node = (payload.get("data") or {}).get("node") or {}

        # Locate the Status single-select field.
        status_field: dict[str, Any] | None = None
        for f in (node.get("fields") or {}).get("nodes") or []:
            if f and f.get("name", "").lower() == "status":
                status_field = f
                break

        columns: list[dict[str, Any]] = []
        if status_field:
            for opt in status_field.get("options") or []:
                columns.append(
                    {
                        "id": opt["id"],
                        "name": opt["name"],
                        # GitHub returns colour names like "GREEN"; map them to
                        # hex approximations the front-end can display directly.
                        "color": _project_color(opt.get("color") or ""),
                    }
                )

        issues: list[dict[str, Any]] = []
        for item in (node.get("items") or {}).get("nodes") or []:
            if not item:
                continue
            content = item.get("content") or {}
            if not content.get("number"):
                # Skip non-issue items (draft notes, pull requests, …).
                continue
            fv = item.get("fieldValueByName") or {}
            issues.append(
                {
                    "item_id": item["id"],
                    "column_id": fv.get("optionId"),
                    "number": content["number"],
                    "title": content["title"],
                    "state": content.get("state", "open").lower(),
                    "url": content.get("url") or "",
                    "body": content.get("body") or "",
                    "labels": [
                        {
                            "name": lbl["name"],
                            "color": (lbl.get("color") or "888888").lstrip("#"),
                        }
                        for lbl in (content.get("labels") or {}).get("nodes") or []
                    ],
                    "updated_at": content.get("updatedAt"),
                }
            )

        return {
            "project_id": project_node_id,
            "field_id": status_field["id"] if status_field else None,
            "columns": columns,
            "issues": issues,
        }

    async def move_project_item(
        self,
        project_node_id: str,
        item_node_id: str,
        field_node_id: str,
        option_id: str,
    ) -> None:
        """Move a project item into a different Status column."""
        mutation = (
            "mutation($proj:ID!,$item:ID!,$field:ID!,$opt:String!){"
            "updateProjectV2ItemFieldValue(input:{"
            "projectId:$proj"
            " itemId:$item"
            " fieldId:$field"
            " value:{singleSelectOptionId:$opt}"
            "}){projectV2Item{id}}"
            "}"
        )
        r = await self._client.post(
            "/graphql",
            json={
                "query": mutation,
                "variables": {
                    "proj": project_node_id,
                    "item": item_node_id,
                    "field": field_node_id,
                    "opt": option_id,
                },
            },
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            raise ValueError(f"GraphQL errors: {payload['errors']}")

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
