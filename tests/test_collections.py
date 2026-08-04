"""Tests for Collections — CRUD, default protection, tree filtering, cascades.

The suite shares one SQLite file (see ``conftest``), so every test uses unique
collection names and asserts on membership rather than absolute counts.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def _default_collection(client: TestClient) -> dict:
    collections = client.get("/api/collections").json()
    defaults = [c for c in collections if c["is_default"]]
    assert len(defaults) == 1
    return defaults[0]


def _make(client: TestClient, name: str, **extra: object) -> dict:
    r = client.post("/api/collections", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _titles(client: TestClient, collection_id: int) -> set[str]:
    tree = client.get(f"/api/topics/tree?collection_id={collection_id}").json()
    return {node["title"] for node in tree}


def test_default_collection_seeded_and_protected() -> None:
    app = create_app()
    with TestClient(app) as client:
        default = _default_collection(client)
        assert default["name"] == "General"
        assert default["slug"] == "general"

        # The default cannot be renamed or deleted.
        renamed = client.patch(f"/api/collections/{default['id']}", json={"name": "x"})
        assert renamed.status_code == 400
        assert client.delete(f"/api/collections/{default['id']}").status_code == 400

        # …but its repo/accent remain editable (restored afterwards so later
        # tests still see a pristine default — the DB is shared).
        r = client.patch(
            f"/api/collections/{default['id']}",
            json={"github_repo": "acme/inbox", "accent": "emerald"},
        )
        assert r.status_code == 200
        assert r.json()["github_repo"] == "acme/inbox"
        assert r.json()["accent"] == "emerald"
        client.patch(
            f"/api/collections/{default['id']}", json={"github_repo": None, "accent": "sky"}
        )


def test_collection_crud_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        col = _make(client, "Crud", description="Day job", github_repo="acme/work")
        assert col["slug"] == "crud"
        assert col["is_default"] is False
        assert col["topic_count"] == 0
        cid = col["id"]

        # Reserved default name + duplicate (case-insensitive) are rejected.
        assert client.post("/api/collections", json={"name": "General"}).status_code == 400
        assert client.post("/api/collections", json={"name": "crud"}).status_code == 409

        # An unknown accent is rejected by the schema.
        bad = client.post("/api/collections", json={"name": "Bad", "accent": "chartreuse"})
        assert bad.status_code == 422

        r = client.patch(f"/api/collections/{cid}", json={"name": "Crud renamed"})
        assert r.status_code == 200
        assert r.json()["name"] == "Crud renamed"

        assert client.delete(f"/api/collections/{cid}").status_code == 204
        assert client.get(f"/api/collections/{cid}").status_code == 404


def test_new_topics_land_in_the_default_collection() -> None:
    app = create_app()
    with TestClient(app) as client:
        default = _default_collection(client)
        topic = client.post("/api/topics", json={"title": "Seeded inbox"}).json()
        assert topic["collection_id"] == default["id"]

        counts = {c["id"]: c["topic_count"] for c in client.get("/api/collections").json()}
        assert counts[default["id"]] >= 1


def test_tree_is_filtered_by_collection() -> None:
    app = create_app()
    with TestClient(app) as client:
        left = _make(client, "Filter left")
        right = _make(client, "Filter right")

        here = client.post("/api/topics", json={"title": "Stays put"}).json()
        there = client.post("/api/topics", json={"title": "Moves away"}).json()
        client.patch(f"/api/topics/{here['id']}", json={"collection_id": left["id"]})
        client.patch(f"/api/topics/{there['id']}", json={"collection_id": right["id"]})

        assert _titles(client, left["id"]) == {"Stays put"}
        assert _titles(client, right["id"]) == {"Moves away"}

        # Without the filter the tree is unscoped.
        unscoped = {node["title"] for node in client.get("/api/topics/tree").json()}
        assert {"Stays put", "Moves away"} <= unscoped


def test_moving_a_topic_cascades_to_descendants() -> None:
    app = create_app()
    with TestClient(app) as client:
        target = _make(client, "Cascade target")
        parent = client.post("/api/topics", json={"title": "Cascade parent"}).json()
        child = client.post(
            "/api/topics", json={"title": "Cascade child", "parent_id": parent["id"]}
        ).json()
        grandchild = client.post(
            "/api/topics", json={"title": "Cascade grandchild", "parent_id": child["id"]}
        ).json()

        # Children inherit their parent's collection on creation.
        assert child["collection_id"] == parent["collection_id"]

        client.patch(f"/api/topics/{parent['id']}", json={"collection_id": target["id"]})
        for tid in (child["id"], grandchild["id"]):
            assert client.get(f"/api/topics/{tid}").json()["collection_id"] == target["id"]


def test_reparenting_adopts_the_new_parents_collection() -> None:
    app = create_app()
    with TestClient(app) as client:
        target = _make(client, "Reparent target")
        host = client.post("/api/topics", json={"title": "Reparent host"}).json()
        client.patch(f"/api/topics/{host['id']}", json={"collection_id": target["id"]})

        orphan = client.post("/api/topics", json={"title": "Reparent orphan"}).json()
        leaf = client.post(
            "/api/topics", json={"title": "Reparent leaf", "parent_id": orphan["id"]}
        ).json()

        client.patch(f"/api/topics/{orphan['id']}", json={"parent_id": host["id"]})
        assert client.get(f"/api/topics/{orphan['id']}").json()["collection_id"] == target["id"]
        assert client.get(f"/api/topics/{leaf['id']}").json()["collection_id"] == target["id"]


def test_deleting_a_collection_rehomes_its_topics() -> None:
    app = create_app()
    with TestClient(app) as client:
        default = _default_collection(client)
        doomed = _make(client, "Delete doomed")
        destination = _make(client, "Delete destination")

        topic = client.post("/api/topics", json={"title": "Rehomed"}).json()
        client.patch(f"/api/topics/{topic['id']}", json={"collection_id": doomed["id"]})

        # An explicit destination wins…
        url = f"/api/collections/{doomed['id']}?reassign_to={destination['id']}"
        assert client.delete(url).status_code == 204
        assert client.get(f"/api/topics/{topic['id']}").json()["collection_id"] == destination["id"]

        # …and without one, topics fall back to the default collection.
        assert client.delete(f"/api/collections/{destination['id']}").status_code == 204
        assert client.get(f"/api/topics/{topic['id']}").json()["collection_id"] == default["id"]


def test_repo_precedence_topic_then_collection_then_global() -> None:
    """topic.github_repo → collection.github_repo → the global setting."""
    import anyio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Topic
    from precursor.backend.services.collections import resolve_topic_github_repo

    app = create_app()
    with TestClient(app) as client:
        # PUT merges (exclude_unset), so this only touches the repo setting.
        assert client.put("/api/settings", json={"github_repo": "acme/global"}).status_code == 200
        scoped = _make(client, "Precedence", github_repo="acme/collection")
        topic = client.post("/api/topics", json={"title": "Precedence topic"}).json()

        async def resolve(topic_id: int) -> str | None:
            async with SessionLocal() as session:
                row = await session.get(Topic, topic_id)
                assert row is not None
                return await resolve_topic_github_repo(session, row)

        # Default collection has no override → the global setting.
        assert anyio.run(resolve, topic["id"]) == "acme/global"

        # Inside a collection with an override → the collection repo.
        client.patch(f"/api/topics/{topic['id']}", json={"collection_id": scoped["id"]})
        assert anyio.run(resolve, topic["id"]) == "acme/collection"

        # A topic-level repo always wins.
        client.patch(f"/api/topics/{topic['id']}", json={"github_repo": "acme/topic"})
        assert anyio.run(resolve, topic["id"]) == "acme/topic"
