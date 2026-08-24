"""ProjectsV2 GraphQL calls, layered on core's :class:`GitHubClient`.

Everything here is Projects-specific, which is why it lives in the plugin
rather than core: the host only maintains the generic REST/GraphQL transport
(auth headers, error typing, scope detection) and this subclass adds the board
queries on top.
"""

from __future__ import annotations

from typing import Any

from precursor.plugin_api import GitHubClient, GitHubRepoNotAccessibleError


class ProjectsClient(GitHubClient):
    """GitHub client extended with the Projects v2 board operations."""

    async def list_repo_projects(self, repo: str) -> list[dict[str, Any]]:
        """List the configured repo owner's open ProjectsV2 (newest first)."""
        owner, _name = self.split_repo(repo)
        return await self.list_owner_projects(owner)

    async def list_owner_projects(self, owner: str) -> list[dict[str, Any]]:
        """List one account's open ProjectsV2 (newest first).

        ProjectsV2 are owned by a user/org and are only *optionally* linked to a
        repository, so scoping to the owner (rather than
        ``repository.projectsV2``) surfaces every board the account has —
        including ones not linked to any repo, and boards belonging to an
        account other than the configured repo's.
        """
        query = (
            "query($o:String!){"
            "repositoryOwner(login:$o){"
            "... on ProjectV2Owner{"
            "projectsV2(first:50,orderBy:{field:UPDATED_AT,direction:DESC}){"
            "nodes{id number title url closed shortDescription}"
            "}}}}"
        )
        data = await self.graphql(query, {"o": owner}, raise_on_error=False)
        owner_node = data.get("repositoryOwner")
        if owner_node is None:
            raise GitHubRepoNotAccessibleError(owner)
        nodes = (owner_node.get("projectsV2") or {}).get("nodes") or []
        return [self._project(p, owner) for p in nodes if not p.get("closed")]

    async def get_owner_project(self, owner: str, number: int) -> dict[str, Any] | None:
        """One specific board by owner + per-owner number, or ``None``.

        Lets a user pin a single project from an account whose *other* boards
        they have no interest in (a customer's roadmap, say) without listing
        everything that account owns.
        """
        query = (
            "query($o:String!,$n:Int!){"
            "repositoryOwner(login:$o){"
            "... on ProjectV2Owner{"
            "projectV2(number:$n){id number title url closed shortDescription}"
            "}}}"
        )
        data = await self.graphql(query, {"o": owner, "n": number}, raise_on_error=False)
        owner_node = data.get("repositoryOwner")
        if owner_node is None:
            raise GitHubRepoNotAccessibleError(owner)
        node = owner_node.get("projectV2")
        if not node or node.get("closed"):
            return None
        return self._project(node, owner)

    @staticmethod
    def _project(node: dict[str, Any], owner: str) -> dict[str, Any]:
        """Normalise a ProjectV2 node, tagging it with the account that owns it.

        The owner matters once boards can come from several accounts: two of them
        called "Roadmap" are otherwise indistinguishable in the picker.
        """
        return {
            "id": node["id"],
            "number": node["number"],
            "title": node["title"],
            "url": node.get("url"),
            "closed": bool(node.get("closed")),
            "short_description": node.get("shortDescription"),
            "owner": owner,
        }

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
            data = await self.graphql(
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
        data = await self.graphql(
            mutation,
            {"p": project_id, "i": item_id, "f": field_id, "o": option_id},
        )
        updated = (data.get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") or {}
        return str(updated.get("id") or item_id)

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
