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
            "user": (c.get("user") or {}).get("login") or "",
            "body": c.get("body") or "",
            "updated_at": c.get("updated_at") or c.get("created_at") or "",
        }

    async def set_issue_labels(
        self, repo: str, number: int, labels: list[str]
    ) -> list[dict[str, Any]]:
        """Replace an issue's labels with ``labels`` and return the new set."""
        owner, name = self._split(repo)
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

    async def _graphql(
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

    async def list_repo_projects(self, repo: str) -> list[dict[str, Any]]:
        """List the repo owner's open ProjectsV2 (newest first).

        ProjectsV2 are owned by a user/org and are only *optionally* linked to a
        repository, so scoping to the owner (rather than
        ``repository.projectsV2``) surfaces every board the account has —
        including ones not linked to any repo. ``repo`` is still the input
        because the kanban mode is gated on a configured ``owner/name``.
        """
        owner, _name = self._split(repo)
        query = (
            "query($o:String!){"
            "repositoryOwner(login:$o){"
            "... on ProjectV2Owner{"
            "projectsV2(first:50,orderBy:{field:UPDATED_AT,direction:DESC}){"
            "nodes{id number title url closed shortDescription}"
            "}}}}"
        )
        data = await self._graphql(query, {"o": owner}, raise_on_error=False)
        owner_node = data.get("repositoryOwner")
        if owner_node is None:
            raise GitHubRepoNotAccessibleError(repo)
        nodes = (owner_node.get("projectsV2") or {}).get("nodes") or []
        return [
            {
                "id": p["id"],
                "number": p["number"],
                "title": p["title"],
                "url": p.get("url"),
                "closed": bool(p.get("closed")),
                "short_description": p.get("shortDescription"),
            }
            for p in nodes
            if not p.get("closed")
        ]

    async def get_project_board(
        self, project_id: str, *, status_field_name: str = "Status"
    ) -> dict[str, Any]:
        """Return a project's Status single-select field + all its items.

        Columns are derived from the Status field's options; items are paged
        exhaustively so the board reflects the whole project.
        """
        query = (
            "query($id:ID!,$field:String!,$after:String){"
            "node(id:$id){... on ProjectV2{"
            "id title url "
            "field(name:$field){... on ProjectV2SingleSelectField{"
            "id name options{id name}}}"
            "items(first:100,after:$after){"
            "pageInfo{hasNextPage endCursor}"
            "nodes{id "
            "fieldValueByName(name:$field){"
            "... on ProjectV2ItemFieldSingleSelectValue{optionId name}}"
            "content{"
            "__typename "
            "... on Issue{number title url state stateReason "
            "repository{nameWithOwner}"
            "labels(first:20){nodes{name color}}}"
            "... on PullRequest{number title url state "
            "repository{nameWithOwner}"
            "labels(first:20){nodes{name color}}}"
            "... on DraftIssue{title}}"
            "}}}}}"
        )
        title = ""
        url: str | None = None
        status_field: dict[str, Any] | None = None
        items: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            data = await self._graphql(
                query,
                {"id": project_id, "field": status_field_name, "after": after},
                raise_on_error=False,
            )
            node = data.get("node")
            if not node:
                raise ValueError(f"Project '{project_id}' not found or not accessible")
            title = node.get("title") or ""
            url = node.get("url")
            if status_field is None:
                field = node.get("field") or {}
                status_field = {
                    "id": field.get("id"),
                    "name": field.get("name"),
                    "options": [
                        {"id": o["id"], "name": o["name"]} for o in (field.get("options") or [])
                    ],
                }
            item_conn = node.get("items") or {}
            for it in item_conn.get("nodes") or []:
                summary = self._project_item(it)
                if summary is not None:
                    items.append(summary)
            page = item_conn.get("pageInfo") or {}
            if page.get("hasNextPage") and page.get("endCursor"):
                after = page["endCursor"]
                continue
            break
        return {
            "id": project_id,
            "title": title,
            "url": url,
            "status_field": status_field,
            "items": items,
        }

    async def set_project_item_status(
        self, *, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> str:
        """Move an item to a Status option; returns the updated item id."""
        mutation = (
            "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){"
            "updateProjectV2ItemFieldValue(input:{"
            "projectId:$p,itemId:$i,fieldId:$f,"
            "value:{singleSelectOptionId:$o}}){"
            "projectV2Item{id}}}"
        )
        data = await self._graphql(
            mutation,
            {"p": project_id, "i": item_id, "f": field_id, "o": option_id},
        )
        updated = (data.get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") or {}
        return updated.get("id") or item_id

    @staticmethod
    def _project_item(item: dict[str, Any]) -> dict[str, Any] | None:
        """Normalise a ProjectV2 item node into a board card.

        Draft issues (no repo content) are skipped — the board only surfaces
        real issues and pull requests.
        """
        content = item.get("content") or {}
        typename = content.get("__typename")
        if typename not in ("Issue", "PullRequest"):
            return None
        status = item.get("fieldValueByName") or {}
        labels = (content.get("labels") or {}).get("nodes") or []
        repository = (content.get("repository") or {}).get("nameWithOwner")
        return {
            "id": item["id"],
            "type": "pull_request" if typename == "PullRequest" else "issue",
            "number": content.get("number"),
            "title": content.get("title") or "",
            "url": content.get("url"),
            "state": content.get("state"),
            "repo": repository,
            "status_option_id": status.get("optionId"),
            "status_name": status.get("name"),
            "labels": [
                {"name": label["name"], "color": label.get("color") or "888888"} for label in labels
            ],
        }

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
