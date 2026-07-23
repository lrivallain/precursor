"""Smoke tests for the FastAPI app — ensures imports + lifespan wire up cleanly."""

from __future__ import annotations

import io
import re
import zipfile

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import chat as chat_router
from precursor.backend.services import turn_engine as turn_engine_mod
from precursor.backend.services.llm.base import TextDeltaEvent, TurnDoneEvent, UsageEvent


def _build_docx_bytes(text: str, *, header_text: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>"
                f"{text}"
                "</w:t></w:r></w:p></w:body></w:document>"
            ),
        )
        if header_text:
            zf.writestr(
                "word/header1.xml",
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:p><w:r><w:t>"
                    f"{header_text}"
                    "</w:t></w:r></w:p></w:hdr>"
                ),
            )
    return buf.getvalue()


def _build_pptx_bytes(text: str, *, notes_text: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:cSld><p:spTree><p:sp><p:txBody><a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<a:r><a:t>{text}</a:t></a:r>"
                "</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            ),
        )
        if notes_text:
            zf.writestr(
                "ppt/notesSlides/notesSlide1.xml",
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                    '<p:cSld><p:spTree><p:sp><p:txBody><a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    f"<a:r><a:t>{notes_text}</a:t></a:r>"
                    "</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
                ),
            )
    return buf.getvalue()


def _build_pdf_bytes(text: str) -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 64>>stream\nBT /F1 12 Tf 72 72 Td ("
        + text.encode("latin-1", errors="ignore")
        + b") Tj ET\nendstream endobj\nxref\n0 5\n0000000000 65535 f \n"
        b"trailer<</Root 1 0 R/Size 5>>\nstartxref\n0\n%%EOF\n"
    )


def test_create_app_and_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body.get("version"), str) and body["version"]


def test_topics_empty_tree() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/topics/tree")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_chats_crud_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Empty to start.
        r = client.get("/api/chats")
        assert r.status_code == 200
        assert r.json() == []

        # Create a chat.
        r = client.post("/api/chats", json={"title": "My first chat"})
        assert r.status_code == 201
        chat = r.json()
        assert chat["title"] == "My first chat"
        # Default chat slug is a random UUID hex (not derived from the title),
        # so repeated "New chat" creates don't pile up new-chat-2, new-chat-3…
        assert re.fullmatch(r"[0-9a-f]{32}", chat["slug"])
        assert chat["unread_count"] == 0
        chat_id = chat["id"]
        slug = chat["slug"]

        # An explicit slug is still honoured (and slugified).
        r2 = client.post("/api/chats", json={"title": "Other", "slug": "My Custom Slug"})
        assert r2.status_code == 201
        assert r2.json()["slug"] == "my-custom-slug"
        client.delete(f"/api/chats/{r2.json()['id']}")

        # Resolvable by slug for /chats/<slug> deep links.
        r = client.get(f"/api/chats/by-slug/{slug}")
        assert r.status_code == 200
        assert r.json()["id"] == chat_id
        assert client.get("/api/chats/by-slug/nope").status_code == 404

        # It shows up in the list.
        r = client.get("/api/chats")
        assert [c["id"] for c in r.json()] == [chat_id]

        # Messages endpoint exists and is empty.
        r = client.get(f"/api/chats/{chat_id}/messages")
        assert r.status_code == 200
        assert r.json() == []

        # Update it.
        r = client.patch(f"/api/chats/{chat_id}", json={"title": "Renamed", "pinned": True})
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed"
        assert r.json()["pinned"] is True

        # Archive / unarchive.
        r = client.post(f"/api/chats/{chat_id}/archive")
        assert r.status_code == 200
        assert r.json()["archived_at"] is not None
        assert client.get("/api/chats").json() == []
        assert [c["id"] for c in client.get("/api/chats/archived").json()] == [chat_id]

        r = client.post(f"/api/chats/{chat_id}/unarchive")
        assert r.status_code == 200
        assert r.json()["archived_at"] is None

        # Mark read.
        assert client.post(f"/api/chats/{chat_id}/read").status_code == 204

        # Delete.
        assert client.delete(f"/api/chats/{chat_id}").status_code == 204
        assert client.get("/api/chats").json() == []
        assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_chat_message_serialization_handles_null_topic_id() -> None:
    """Chat messages have topic_id=None; the read model must allow it.

    Regression: MessageRead.topic_id was a required int, so GET on a chat's
    messages 500'd with a ResponseValidationError.
    """
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Notes chat"}).json()["id"]
        # /notes append persists a user message without invoking the LLM.
        r = client.post(f"/api/chats/{cid}/messages/notes/append", json={"text": "hello"})
        assert r.status_code == 200
        msg = r.json()["message"]
        assert msg["topic_id"] is None
        assert msg["chat_id"] == cid

        r = client.get(f"/api/chats/{cid}/messages")
        assert r.status_code == 200
        msgs = r.json()
        assert len(msgs) == 1
        assert msgs[0]["topic_id"] is None
        assert msgs[0]["chat_id"] == cid


def test_topic_notes_draft_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Drafts"}).json()["id"]

        r = client.get(f"/api/topics/{tid}/commands/notes/draft")
        assert r.status_code == 200
        assert r.json() == {"text": None, "updated_at": None, "attachments": []}

        r = client.put(
            f"/api/topics/{tid}/commands/notes/draft",
            json={"text": "meeting rough notes"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["text"] == "meeting rough notes"
        assert isinstance(payload["updated_at"], str)

        r = client.get(f"/api/topics/{tid}/commands/notes/draft")
        assert r.status_code == 200
        assert r.json()["text"] == "meeting rough notes"
        assert r.json()["attachments"] == []

        r = client.delete(f"/api/topics/{tid}/commands/notes/draft")
        assert r.status_code == 204

        r = client.get(f"/api/topics/{tid}/commands/notes/draft")
        assert r.status_code == 200
        assert r.json() == {"text": None, "updated_at": None, "attachments": []}


def test_chat_notes_draft_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Drafts chat"}).json()["id"]

        r = client.get(f"/api/chats/{cid}/messages/notes/draft")
        assert r.status_code == 200
        assert r.json() == {"text": None, "updated_at": None, "attachments": []}

        r = client.put(
            f"/api/chats/{cid}/messages/notes/draft",
            json={"text": "capture this before sending"},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["text"] == "capture this before sending"
        assert isinstance(payload["updated_at"], str)

        r = client.get(f"/api/chats/{cid}/messages/notes/draft")
        assert r.status_code == 200
        assert r.json()["text"] == "capture this before sending"
        assert r.json()["attachments"] == []

        r = client.delete(f"/api/chats/{cid}/messages/notes/draft")
        assert r.status_code == 204

        r = client.get(f"/api/chats/{cid}/messages/notes/draft")
        assert r.status_code == 200
        assert r.json() == {"text": None, "updated_at": None, "attachments": []}


def test_topic_notes_attachments_crud_and_append() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Topic notes images"}).json()["id"]

        upload = client.post(
            f"/api/topics/{tid}/commands/notes/attachments",
            files={"file": ("shot.png", b"fake-image", "image/png")},
        )
        assert upload.status_code == 201
        att = upload.json()
        assert att["mime"] == "image/png"
        assert isinstance(att["id"], int)

        listed = client.get(f"/api/topics/{tid}/commands/notes/attachments")
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [att["id"]]

        notes = client.get(f"/api/topics/{tid}/commands/notes/draft").json()
        assert len(notes["attachments"]) == 1
        assert notes["attachments"][0]["id"] == att["id"]

        appended = client.post(
            f"/api/topics/{tid}/commands/notes/append",
            json={"text": "", "attachment_ids": [att["id"]]},
        )
        assert appended.status_code == 200
        message = appended.json()["message"]
        assert message["content"] == "**Notes**"
        assert len(message["attachments"]) == 1
        assert message["attachments"][0]["message_id"] == message["id"]

        listed_after = client.get(f"/api/topics/{tid}/commands/notes/attachments")
        assert listed_after.status_code == 200
        assert listed_after.json() == []


def test_chat_notes_attachments_delete_and_draft_cleanup() -> None:
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Chat notes images"}).json()["id"]

        upload = client.post(
            f"/api/chats/{cid}/messages/notes/attachments",
            files={"file": ("shot.png", b"fake-image", "image/png")},
        )
        assert upload.status_code == 201
        att = upload.json()

        # The attachment bytes are served from the dedicated note endpoint.
        served = client.get(f"/api/notes/attachments/{att['id']}")
        assert served.status_code == 200
        assert served.content == b"fake-image"

        removed = client.delete(f"/api/chats/{cid}/messages/notes/attachments/{att['id']}")
        assert removed.status_code == 204
        assert client.get(f"/api/chats/{cid}/messages/notes/attachments").json() == []

        # Re-upload and verify deleting the draft cascades the draft attachment.
        reupload = client.post(
            f"/api/chats/{cid}/messages/notes/attachments",
            files={"file": ("shot2.png", b"fake-image-2", "image/png")},
        )
        assert reupload.status_code == 201
        att2 = reupload.json()["id"]
        assert client.delete(f"/api/chats/{cid}/messages/notes/draft").status_code == 204
        assert client.get(f"/api/notes/attachments/{att2}").status_code == 404


def test_topic_attachment_upload_accepts_additional_document_types() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Doc attachments"}).json()["id"]
        cases = [
            ("doc.pdf", _build_pdf_bytes("pdf-text"), "application/pdf"),
            (
                "doc.docx",
                _build_docx_bytes("docx-text"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                "deck.pptx",
                _build_pptx_bytes("pptx-text"),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        ]
        for filename, payload, mime in cases:
            upload = client.post(
                f"/api/topics/{tid}/attachments",
                files={"file": (filename, payload, mime)},
            )
            assert upload.status_code == 201
            att = upload.json()
            assert att["mime"] == mime
            served = client.get(f"/api/attachments/{att['id']}")
            assert served.status_code == 200
            assert served.content == payload


def test_topic_attachment_upload_accepts_text_and_code_files() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Text attachments"}).json()["id"]
        cases = [
            ("notes.txt", b"plain-text", "text/plain", "text/plain"),
            ("readme.md", b"# Title", "text/markdown", "text/markdown"),
            ("data.csv", b"a,b\n1,2", "text/csv", "text/csv"),
            ("config.json", b'{"k": 1}', "application/json", "application/json"),
            # Browsers report unreliable MIMEs for source files; normalize by extension.
            ("script.py", b"print('hi')", "application/octet-stream", "text/x-python"),
            ("app.ts", b"const x = 1;", "video/mp2t", "text/typescript"),
        ]
        for filename, payload, sent_mime, stored_mime in cases:
            upload = client.post(
                f"/api/topics/{tid}/attachments",
                files={"file": (filename, payload, sent_mime)},
            )
            assert upload.status_code == 201, filename
            att = upload.json()
            assert att["mime"] == stored_mime, filename
            served = client.get(f"/api/attachments/{att['id']}")
            assert served.status_code == 200
            assert served.content == payload


def test_topic_attachment_upload_rejects_unsupported_type() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Bad attachments"}).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/attachments",
            files={"file": ("archive.zip", b"PK\x03\x04binary", "application/zip")},
        )
        assert upload.status_code == 415
        assert "Supported types" in upload.text


def test_topic_notes_attachments_accept_docx_and_append() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Notes docs"}).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/commands/notes/attachments",
            files={
                "file": (
                    "notes.docx",
                    _build_docx_bytes("notes-body"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert upload.status_code == 201
        aid = upload.json()["id"]
        appended = client.post(
            f"/api/topics/{tid}/commands/notes/append",
            json={"text": "", "attachment_ids": [aid]},
        )
        assert appended.status_code == 200
        message = appended.json()["message"]
        assert len(message["attachments"]) == 1
        assert (
            message["attachments"][0]["mime"]
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


def test_topic_stream_passes_non_image_attachment_context_to_llm(monkeypatch) -> None:
    class EchoUserPromptProvider:
        name = "echo"

        async def stream_chat(self, *, model, messages, reasoning_effort=None):
            yield ""

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
            _ = model, tools
            last_user = next((m for m in reversed(messages) if m.role == "user"), None)
            yield TextDeltaEvent(content=last_user.content if last_user else "")
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            yield TurnDoneEvent(finish_reason="stop")

        async def list_models(self):
            return []

    async def _fake_get_llm_provider(_session):
        return EchoUserPromptProvider()

    async def _fake_record_usage(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(chat_router, "get_llm_provider", _fake_get_llm_provider)
    monkeypatch.setattr(turn_engine_mod, "record_usage", _fake_record_usage)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Doc context"}).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/attachments",
            files={
                "file": (
                    "brief.docx",
                    _build_docx_bytes("hello from docx attachment"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert upload.status_code == 201
        aid = upload.json()["id"]

        stream = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "please summarize", "attachment_ids": [aid]},
            headers={"Accept": "text/event-stream"},
        )
        assert stream.status_code == 200

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assistant = [m for m in msgs if m["role"] == "assistant"][-1]
        assert "Attached documents:" in assistant["content"]
        assert "hello from docx attachment" in assistant["content"]


def test_topic_stream_extracts_ooxml_header_and_notes_text(monkeypatch) -> None:
    class EchoUserPromptProvider:
        name = "echo"

        async def stream_chat(self, *, model, messages, reasoning_effort=None):
            yield ""

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
            _ = model, tools
            last_user = next((m for m in reversed(messages) if m.role == "user"), None)
            yield TextDeltaEvent(content=last_user.content if last_user else "")
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            yield TurnDoneEvent(finish_reason="stop")

        async def list_models(self):
            return []

    async def _fake_get_llm_provider(_session):
        return EchoUserPromptProvider()

    async def _fake_record_usage(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(chat_router, "get_llm_provider", _fake_get_llm_provider)
    monkeypatch.setattr(turn_engine_mod, "record_usage", _fake_record_usage)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "OOXML context"}).json()["id"]
        doc_upload = client.post(
            f"/api/topics/{tid}/attachments",
            files={
                "file": (
                    "brief.docx",
                    _build_docx_bytes("", header_text="header extracted"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert doc_upload.status_code == 201
        ppt_upload = client.post(
            f"/api/topics/{tid}/attachments",
            files={
                "file": (
                    "deck.pptx",
                    _build_pptx_bytes("", notes_text="speaker notes extracted"),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            },
        )
        assert ppt_upload.status_code == 201

        stream = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={
                "content": "summarize attached docs",
                "attachment_ids": [doc_upload.json()["id"], ppt_upload.json()["id"]],
            },
            headers={"Accept": "text/event-stream"},
        )
        assert stream.status_code == 200

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assistant = [m for m in msgs if m["role"] == "assistant"][-1]
        assert "header extracted" in assistant["content"]
        assert "speaker notes extracted" in assistant["content"]


def test_topic_stream_passes_text_file_content_to_llm(monkeypatch) -> None:
    class EchoUserPromptProvider:
        name = "echo"

        async def stream_chat(self, *, model, messages, reasoning_effort=None):
            yield ""

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
            _ = model, tools
            last_user = next((m for m in reversed(messages) if m.role == "user"), None)
            yield TextDeltaEvent(content=last_user.content if last_user else "")
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            yield TurnDoneEvent(finish_reason="stop")

        async def list_models(self):
            return []

    async def _fake_get_llm_provider(_session):
        return EchoUserPromptProvider()

    async def _fake_record_usage(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(chat_router, "get_llm_provider", _fake_get_llm_provider)
    monkeypatch.setattr(turn_engine_mod, "record_usage", _fake_record_usage)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Text context"}).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/attachments",
            files={
                "file": (
                    "main.py",
                    b"def greet():\n    return 'hi from python file'\n",
                    "text/x-python",
                )
            },
        )
        assert upload.status_code == 201
        aid = upload.json()["id"]

        stream = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "explain this file", "attachment_ids": [aid]},
            headers={"Accept": "text/event-stream"},
        )
        assert stream.status_code == 200

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assistant = [m for m in msgs if m["role"] == "assistant"][-1]
        assert "Attached documents:" in assistant["content"]
        assert "hi from python file" in assistant["content"]


def test_topic_stream_records_model_and_elapsed_on_answer(monkeypatch) -> None:
    """An LLM answer persists the model id, an elapsed_ms duration, and the
    user prompt keeps its created_at timestamp — all surfaced by the read model.
    """

    class EchoProvider:
        name = "echo"

        async def stream_chat(self, *, model, messages, reasoning_effort=None):
            yield ""

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
            _ = model, tools, messages, reasoning_effort
            yield TextDeltaEvent(content="hi there")
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            yield TurnDoneEvent(finish_reason="stop")

        async def list_models(self):
            return []

    async def _fake_get_llm_provider(_session):
        return EchoProvider()

    monkeypatch.setattr(chat_router, "get_llm_provider", _fake_get_llm_provider)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Meta"}).json()["id"]
        stream = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "hello", "model": "test-model-x"},
            headers={"Accept": "text/event-stream"},
        )
        assert stream.status_code == 200
        assert "test-model-x" in stream.text
        assert "elapsed_ms" in stream.text

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        user = [m for m in msgs if m["role"] == "user"][-1]
        assistant = [m for m in msgs if m["role"] == "assistant"][-1]

        assert user["created_at"]
        assert user["model"] is None
        assert user["elapsed_ms"] is None

        assert assistant["model"] == "test-model-x"
        assert isinstance(assistant["elapsed_ms"], int)
        assert assistant["elapsed_ms"] >= 0
        assert assistant["created_at"]


def test_topic_notes_add_and_ask_stream_binds_note_attachments() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Notes stream bind"}).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/commands/notes/attachments",
            files={"file": ("shot.png", b"stream-image", "image/png")},
        )
        assert upload.status_code == 201
        aid = upload.json()["id"]

        stream = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "**Notes**", "note_attachment_ids": [aid]},
            headers={"Accept": "text/event-stream"},
        )
        assert stream.status_code == 200

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert user_msgs
        assert len(user_msgs[-1]["attachments"]) == 1
        assert user_msgs[-1]["attachments"][0]["mime"] == "image/png"


def test_notes_post_comment_uploads_images_and_rewrites_body(monkeypatch) -> None:
    from precursor.backend.routers import commands as commands_router

    posted: dict[str, str] = {}

    async def _fake_require_token(_session) -> str:
        return "test-token"

    async def _fake_upload(self, repo, number, *, filename, content, mime) -> str:
        raise AssertionError("image upload should not be attempted for GitHub comments")

    async def _fake_add_comment(self, repo, number, body) -> dict[str, object]:
        posted["body"] = body
        return {
            "id": 7,
            "url": "https://github.com/octo/example/issues/40#issuecomment-7",
            "body": body,
        }

    async def _fake_aclose(self) -> None:
        return None

    monkeypatch.setattr(commands_router, "_require_token", _fake_require_token)
    monkeypatch.setattr(
        commands_router.GitHubClient, "upload_issue_comment_attachment", _fake_upload
    )
    monkeypatch.setattr(commands_router.GitHubClient, "add_issue_comment", _fake_add_comment)
    monkeypatch.setattr(commands_router.GitHubClient, "aclose", _fake_aclose)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post(
            "/api/topics",
            json={"title": "GH notes", "github_repo": "octo/example", "github_issue_number": 40},
        ).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/commands/notes/attachments",
            files={"file": ("proof.png", b"evidence", "image/png")},
        )
        assert upload.status_code == 201
        aid = upload.json()["id"]

        r = client.post(
            f"/api/topics/{tid}/commands/gh-update/post",
            json={
                "body": f"See image: ![proof](/api/notes/attachments/{aid})",
                "note_attachment_ids": [aid],
            },
        )
        assert r.status_code == 200
        assert "(image kept in chat: proof.png)" in posted["body"]
        assert f"/api/notes/attachments/{aid}" not in posted["body"]
        payload = r.json()
        assert payload["note_upload_failures"] == []
        local = payload["local_note_message"]
        assert local is not None
        assert local["role"] == "user"
        assert len(local["attachments"]) == 1
        assert local["attachments"][0]["original_filename"] == "proof.png"


def test_notes_post_comment_continues_when_image_upload_fails(monkeypatch) -> None:
    from precursor.backend.routers import commands as commands_router

    posted: dict[str, str] = {}

    async def _fake_require_token(_session) -> str:
        return "test-token"

    async def _fake_upload(self, repo, number, *, filename, content, mime) -> str:
        raise AssertionError("image upload should not be attempted for GitHub comments")

    async def _fake_add_comment(self, repo, number, body) -> dict[str, object]:
        posted["body"] = body
        return {
            "id": 8,
            "url": "https://github.com/octo/example/issues/40#issuecomment-8",
            "body": body,
        }

    async def _fake_aclose(self) -> None:
        return None

    monkeypatch.setattr(commands_router, "_require_token", _fake_require_token)
    monkeypatch.setattr(
        commands_router.GitHubClient, "upload_issue_comment_attachment", _fake_upload
    )
    monkeypatch.setattr(commands_router.GitHubClient, "add_issue_comment", _fake_add_comment)
    monkeypatch.setattr(commands_router.GitHubClient, "aclose", _fake_aclose)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post(
            "/api/topics",
            json={"title": "GH notes", "github_repo": "octo/example", "github_issue_number": 40},
        ).json()["id"]
        upload = client.post(
            f"/api/topics/{tid}/commands/notes/attachments",
            files={"file": ("proof.png", b"evidence", "image/png")},
        )
        assert upload.status_code == 201
        aid = upload.json()["id"]

        r = client.post(
            f"/api/topics/{tid}/commands/gh-update/post",
            json={
                "body": f"See image: ![proof](/api/notes/attachments/{aid})",
                "note_attachment_ids": [aid],
            },
        )
        assert r.status_code == 200
        assert "(image kept in chat: proof.png)" in posted["body"]
        payload = r.json()
        assert payload["note_upload_failures"] == []
        local = payload["local_note_message"]
        assert local is not None
        assert local["role"] == "user"
        assert len(local["attachments"]) == 1
        assert local["attachments"][0]["original_filename"] == "proof.png"


def test_create_topic_with_linked_issue(monkeypatch) -> None:
    """create_linked_issue opens an issue titled with the parent chain + links it."""
    from precursor.backend.services import topic_issue

    created: dict[str, object] = {}

    async def _fake_token(_session) -> str:
        return "test-token"

    async def _fake_create_issue(self, repo, *, title, body=None, labels=None):
        created["repo"] = repo
        created["title"] = title
        created["body"] = body
        return {"number": 77, "title": title, "state": "open", "url": None}

    async def _fake_aclose(self) -> None:
        return None

    monkeypatch.setattr(topic_issue, "resolve_github_token", _fake_token)
    monkeypatch.setattr(topic_issue.GitHubClient, "create_issue", _fake_create_issue)
    monkeypatch.setattr(topic_issue.GitHubClient, "aclose", _fake_aclose)

    app = create_app()
    with TestClient(app) as client:
        root = client.post("/api/topics", json={"title": "Root"}).json()
        child = client.post("/api/topics", json={"title": "Child", "parent_id": root["id"]}).json()

        r = client.post(
            "/api/topics",
            json={
                "title": "Leaf",
                "description": "issue body here",
                "parent_id": child["id"],
                "github_repo": "octo/example",
                "create_linked_issue": True,
            },
        )
        assert r.status_code == 201
        topic = r.json()
        # The issue is linked back onto the topic.
        assert topic["github_repo"] == "octo/example"
        assert topic["github_issue_number"] == 77
        # Title carries the ancestor chain, body carries the description.
        assert created["title"] == "[Root / Child] Leaf"
        assert created["body"] == "issue body here"
        assert created["repo"] == "octo/example"


def test_create_topic_with_linked_issue_no_parent_omits_brackets(monkeypatch) -> None:
    """A top-level linked issue uses the bare title (no empty brackets)."""
    from precursor.backend.services import topic_issue

    seen: dict[str, object] = {}

    async def _fake_token(_session) -> str:
        return "test-token"

    async def _fake_create_issue(self, repo, *, title, body=None, labels=None):
        seen["title"] = title
        return {"number": 5, "title": title, "state": "open", "url": None}

    async def _fake_aclose(self) -> None:
        return None

    monkeypatch.setattr(topic_issue, "resolve_github_token", _fake_token)
    monkeypatch.setattr(topic_issue.GitHubClient, "create_issue", _fake_create_issue)
    monkeypatch.setattr(topic_issue.GitHubClient, "aclose", _fake_aclose)

    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/topics",
            json={"title": "Solo", "github_repo": "octo/example", "create_linked_issue": True},
        )
        assert r.status_code == 201
        assert r.json()["github_issue_number"] == 5
        assert seen["title"] == "Solo"


def test_create_topic_linked_issue_requires_repo() -> None:
    """Without a repo (topic or global), the request fails and no topic persists."""
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/topics",
            json={"title": "Orphan", "create_linked_issue": True},
        )
        assert r.status_code == 400
        titles = [t["title"] for t in client.get("/api/topics").json()]
        assert "Orphan" not in titles


def test_chat_promote_to_topic_moves_messages() -> None:
    """Promoting a chat creates a topic, moves the transcript, drops the chat."""
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Promote me", "description": "ctx"}).json()[
            "id"
        ]
        client.post(f"/api/chats/{cid}/messages/notes/append", json={"text": "carry over"})

        r = client.post(f"/api/chats/{cid}/promote")
        assert r.status_code == 200
        topic = r.json()
        assert topic["title"] == "Promote me"
        assert topic["description"] == "ctx"
        tid = topic["id"]

        # The chat is gone, the message moved onto the new topic.
        assert client.get(f"/api/chats/{cid}").status_code == 404
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assert any("carry over" in m["content"] for m in msgs)
        assert all(m["topic_id"] == tid and m["chat_id"] is None for m in msgs)


def test_log_config_unifies_format() -> None:
    import logging

    from precursor.backend.logging_config import UTCFormatter, build_log_config

    cfg = build_log_config("debug", color=False)
    # uvicorn + noisy third-party loggers route through the single root handler.
    assert cfg["root"]["handlers"] == ["default"]
    # Root level follows the app log_level (so precursor.* honours debug)...
    assert cfg["root"]["level"] == "DEBUG"
    # ...but noisy deps stay pinned regardless, so app DEBUG never unleashes
    # per-statement library spam.
    assert cfg["loggers"]["aiosqlite"]["level"] == "WARNING"
    assert cfg["loggers"]["sqlalchemy.engine"]["level"] == "WARNING"
    assert cfg["loggers"]["sse_starlette.sse"]["level"] == "INFO"
    assert cfg["loggers"]["openai._base_client"]["level"] == "WARNING"
    assert cfg["loggers"]["mcp.client"]["level"] == "WARNING"
    for name in ("uvicorn", "uvicorn.access", "mcp", "httpx", "watchfiles"):
        assert cfg["loggers"][name]["handlers"] == []
        assert cfg["loggers"][name]["propagate"] is True
    # Keeps import-time module loggers alive so they propagate to root.
    assert cfg["disable_existing_loggers"] is False

    record = logging.LogRecord(
        "precursor.backend.services.scheduler",
        logging.INFO,
        __file__,
        1,
        "Scheduler started",
        None,
        None,
    )
    line = UTCFormatter(color=False).format(record)
    timestamp = line.split(" ", 1)[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp)
    assert "INFO" in line
    assert "precursor.backend.services.scheduler" in line
    assert line.endswith("Scheduler started")
    # Plain mode emits no ANSI escapes; colour mode wraps the level.
    assert "\033[" not in line
    assert "\033[" in UTCFormatter(color=True).format(record)


def test_chat_messages_cursor_pagination() -> None:
    """Windowed message listing returns the most recent slice, chronological,
    with before_id paging further back; no params returns the full transcript."""
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Long chat"}).json()["id"]
        for i in range(5):
            r = client.post(f"/api/chats/{cid}/messages/notes/append", json={"text": f"m{i}"})
            assert r.status_code == 200

        # Full transcript, oldest first.
        full = client.get(f"/api/chats/{cid}/messages").json()
        assert [m["content"] for m in full] == [
            "**Notes**\n\nm0",
            "**Notes**\n\nm1",
            "**Notes**\n\nm2",
            "**Notes**\n\nm3",
            "**Notes**\n\nm4",
        ]

        # Most recent page of 2, still oldest-first within the page.
        page = client.get(f"/api/chats/{cid}/messages?limit=2").json()
        assert [m["content"] for m in page] == ["**Notes**\n\nm3", "**Notes**\n\nm4"]

        # Page further back using the oldest loaded id as the cursor.
        older = client.get(f"/api/chats/{cid}/messages?limit=2&before_id={page[0]['id']}").json()
        assert [m["content"] for m in older] == ["**Notes**\n\nm1", "**Notes**\n\nm2"]

        # Reaching the start returns the remaining (fewer than limit) rows.
        oldest = client.get(f"/api/chats/{cid}/messages?limit=2&before_id={older[0]['id']}").json()
        assert [m["content"] for m in oldest] == ["**Notes**\n\nm0"]

        # Past the start: empty, signalling no more history.
        assert (
            client.get(f"/api/chats/{cid}/messages?limit=2&before_id={oldest[0]['id']}").json()
            == []
        )
