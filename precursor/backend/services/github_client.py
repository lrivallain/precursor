"""Thin async GitHub REST wrapper — only the bits Precursor needs."""

from __future__ import annotations

import re
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"


class GitHubRepoNotAccessibleError(Exception):
    """The repository can't be resolved for the current token.

    Raised when GitHub reports the repo as nonexistent or invisible (e.g. a
    private repo the token can't see). Lets callers degrade to a friendly
    message instead of surfacing a raw API error.
    """

    def __init__(self, repo: str) -> None:
        super().__init__(f"Repository '{repo}' not found or not accessible")
        self.repo = repo


class GitHubInsufficientScopeError(Exception):
    """The token authenticates but lacks an OAuth scope the query needs.

    GitHub returns this as an ``INSUFFICIENT_SCOPES`` GraphQL error (HTTP 200).
    ProjectsV2 reads require ``read:project`` — a scope the ``repo`` scope does
    not imply — so a token that can see issues may still be rejected here. We
    surface an actionable message instead of the misleading "not accessible".
    """

    def __init__(self, required_scopes: list[str] | None = None) -> None:
        scopes = required_scopes or ["read:project"]
        primary = scopes[0]
        # The read-write ``project`` scope is a superset of ``read:project``, so
        # recommending it alone unblocks both reading boards and moving cards.
        super().__init__(
            f"GitHub token is missing the '{primary}' scope required for "
            "Projects. Grant it with `gh auth refresh -h github.com -s project` "
            "(or add a token with the 'project' scope in Settings), then "
            "restart Precursor."
        )
        self.required_scopes = scopes


_SCOPE_RE = re.compile(r"\['([^']+)'\]")


def _required_scopes(error: dict[str, Any]) -> list[str]:
    """Pull the required scope(s) out of an INSUFFICIENT_SCOPES message.

    GitHub phrases it as: "... requires one of the following scopes:
    ['read:project'], but your token ...". Falls back to ``read:project`` when
    the message can't be parsed.
    """
    message = error.get("message") if isinstance(error, dict) else ""
    match = _SCOPE_RE.search(message or "")
    if match:
        return [s.strip() for s in match.group(1).split(",") if s.strip()]
    return ["read:project"]


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
    def split_repo(repo: str) -> tuple[str, str]:
        if "/" not in repo:
            raise ValueError(f"Invalid repo '{repo}', expected 'owner/name'")
        owner, name = repo.split("/", 1)
        return owner, name

    async def list_issues(
        self, repo: str, *, query: str | None = None, state: str = "open"
    ) -> list[dict[str, Any]]:
        owner, name = self.split_repo(repo)
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
        owner, name = self.split_repo(repo)
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
        owner, name = self.split_repo(repo)
        r = await self._client.get(f"/repos/{owner}/{name}/issues/{number}")
        r.raise_for_status()
        return self._issue_summary(r.json())

    async def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        owner, name = self.split_repo(repo)
        r = await self._client.get(
            f"/repos/{owner}/{name}/issues/{number}/comments", params={"per_page": 100}
        )
        r.raise_for_status()
        return [
            {
                "id": c["id"],
                "user": c["user"]["login"],
                "body": c.get("body") or "",
                "created_at": c.get("created_at"),
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
        owner, name = self.split_repo(repo)
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
        owner, name = self.split_repo(repo)
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
        owner, name = self.split_repo(repo)
        r = await self._client.get(f"/repos/{owner}/{name}/labels", params={"per_page": 100})
        r.raise_for_status()
        return [{"name": label["name"], "color": label["color"]} for label in r.json()]

    async def add_issue_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        owner, name = self.split_repo(repo)
        r = await self._client.post(
            f"/repos/{owner}/{name}/issues/{number}/comments",
            json={"body": body},
        )
        r.raise_for_status()
        c = r.json()
        return {
            "id": c["id"],
            "url": c.get("html_url"),
            "user": (c.get("user") or {}).get("login") or "",
            "body": c.get("body") or "",
            "created_at": c.get("created_at") or c.get("updated_at") or "",
            "updated_at": c.get("updated_at") or c.get("created_at") or "",
        }

    async def set_issue_labels(
        self, repo: str, number: int, labels: list[str]
    ) -> list[dict[str, Any]]:
        """Replace an issue's labels with ``labels`` and return the new set."""
        owner, name = self.split_repo(repo)
        r = await self._client.put(
            f"/repos/{owner}/{name}/issues/{number}/labels",
            json={"labels": labels},
        )
        r.raise_for_status()
        return [
            {"name": label["name"], "color": label.get("color") or "888888"} for label in r.json()
        ]

    async def upload_issue_comment_attachment(
        self,
        repo: str,
        number: int,
        *,
        filename: str,
        content: bytes,
        mime: str,
    ) -> str:
        owner, name = self.split_repo(repo)
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

    async def graphql(
        self, query: str, variables: dict[str, Any], *, raise_on_error: bool = True
    ) -> dict[str, Any]:
        """POST a GraphQL query and return ``data``.

        GitHub returns HTTP 200 even for GraphQL-level errors, so a raw
        ``raise_for_status`` isn't enough. For *mutations* (``raise_on_error``,
        the default) we surface the first error message. For read queries pass
        ``raise_on_error=False``: a NOT_FOUND / no-permission repo comes back as
        HTTP 200 with ``data.<field> = null`` *and* an ``errors`` array, so the
        caller inspects the null field and degrades to a friendly message
        instead of a raw 500 (mirrors ``count_issues_by_state``).
        """
        r = await self._client.post("/graphql", json={"query": query, "variables": variables})
        r.raise_for_status()
        payload = r.json()
        errors = payload.get("errors")
        if errors:
            # A missing OAuth scope is never a "degrade to empty" case — the
            # token simply can't run this query — so raise a typed, actionable
            # error regardless of ``raise_on_error``.
            scope_error = next(
                (
                    e
                    for e in errors
                    if isinstance(e, dict) and e.get("type") == "INSUFFICIENT_SCOPES"
                ),
                None,
            )
            if scope_error is not None:
                raise GitHubInsufficientScopeError(_required_scopes(scope_error))
            if raise_on_error:
                message = (
                    errors[0].get("message") if isinstance(errors[0], dict) else str(errors[0])
                )
                raise RuntimeError(f"GitHub GraphQL error: {message}")
        return payload.get("data") or {}

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

    async def get_copilot_quota(self) -> dict[str, Any] | None:
        """Return the token user's Copilot quota snapshot, or ``None``.

        Hits the same ``/copilot_internal/user`` endpoint the Copilot editors
        use — it exposes ``quota_snapshots`` (chat / completions / premium
        interactions) plus the top-level ``quota_reset_date``. Accounts with no
        Copilot seat get a 403/404, which we treat as "no quota to show" rather
        than an error so the persona degrades quietly.
        """
        r = await self._client.get("/copilot_internal/user")
        if r.status_code in (403, 404):
            return None
        r.raise_for_status()
        payload = r.json()
        return payload if isinstance(payload, dict) else None

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
