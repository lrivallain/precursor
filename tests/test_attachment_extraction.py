"""Document-attachment text extraction: cap, truncation note, and disk cache."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.models import Attachment
from precursor.backend.services import attachment_extraction as extraction
from precursor.backend.services.blob_store import blob_path, gc_orphan_blobs, write_blob
from precursor.backend.services.context_budget import trim_messages
from precursor.backend.services.llm.base import ChatMessage


def _attachment(data: bytes, mime: str = "text/plain", name: str = "doc.txt") -> Attachment:
    sha = write_blob(data)
    return Attachment(id=1, mime=mime, size=len(data), original_filename=name, sha256=sha)


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


def _sidecar(att: Attachment):
    return blob_path(att.sha256).with_name(f"{att.sha256}.{extraction._TEXT_CACHE_SUFFIX}")


def test_long_document_is_no_longer_cut_at_4k() -> None:
    body = "".join(f"line {i:05d} of a long paper\n" for i in range(2_000))  # ~52k chars
    att = _attachment(body.encode())

    context = extraction.attachments_to_text_context([att])

    assert "line 00000" in context
    assert "line 01999" in context
    assert "truncated" not in context


def test_long_pdf_is_extracted_past_the_old_cap() -> None:
    text = "intro " + ("x" * 10_000) + " conclusion"
    att = _attachment(_build_pdf_bytes(text), mime="application/pdf", name="paper.pdf")

    context = extraction.attachments_to_text_context([att])

    assert "conclusion" in context


def test_cap_applies_with_an_explicit_truncation_note() -> None:
    att = _attachment(("a" * 5_000).encode())

    context = extraction.attachments_to_text_context([att], max_chars=1_000)

    assert "a" * 1_000 in context
    assert "a" * 1_001 not in context
    assert "showing the first 1,000 of 5,000 characters" in context
    assert "not visible" in context


def test_extracted_text_is_cached_beside_the_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    att = _attachment(b"cached body text unique-7f3a")
    _sidecar(att).unlink(missing_ok=True)

    assert extraction.extract_attachment_text(att) == "cached body text unique-7f3a"
    assert _sidecar(att).read_text(encoding="utf-8") == "cached body text unique-7f3a"

    def _boom(*_args: object) -> str:
        raise AssertionError("re-extracted despite a cached copy")

    monkeypatch.setattr(extraction, "_extract_text_from_bytes", _boom)
    assert extraction.extract_attachment_text(att) == "cached body text unique-7f3a"


def test_warm_cache_takes_plain_values_and_skips_images() -> None:
    att = _attachment(b"warm me up unique-91c2")
    img = _attachment(b"\x89PNG not really unique-91c2", mime="image/png", name="i.png")
    _sidecar(att).unlink(missing_ok=True)

    extraction.warm_attachment_text_cache([(att.sha256, att.mime), (img.sha256, img.mime)])

    assert _sidecar(att).exists()
    assert not _sidecar(img).exists()


def test_gc_keeps_sidecars_of_referenced_blobs_only() -> None:
    orphan = _attachment(b"orphan document unique-44d1")
    extraction.extract_attachment_text(orphan)
    assert _sidecar(orphan).exists()

    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "GC sidecars"}).json()["id"]
        up = client.post(
            f"/api/topics/{tid}/attachments",
            files={"file": ("kept.txt", b"kept document unique-44d1", "text/plain")},
        )
        assert up.status_code == 201, up.text
        kept = _attachment(b"kept document unique-44d1")
        extraction.extract_attachment_text(kept)

        asyncio.run(gc_orphan_blobs())

        assert blob_path(kept.sha256).exists()
        assert _sidecar(kept).exists()
        assert not blob_path(orphan.sha256).exists()
        assert not _sidecar(orphan).exists()


def test_trim_caps_tool_results_but_not_user_documents() -> None:
    doc = "d" * 50_000
    tool = "t" * 50_000
    out = trim_messages(
        [
            ChatMessage(role="user", content=doc),
            ChatMessage(role="tool", content=tool, tool_call_id="c1"),
        ],
        max_input_tokens=1_000_000,
        per_message_max_tokens=100,
    )

    assert out[0].content == doc
    assert out[1].content is not None and len(out[1].content) < 1_000
    assert "truncated" in out[1].content


def test_attachment_cap_setting_reaches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services import conversation_turn as conversation_turn_mod
    from precursor.backend.services import turn_engine as turn_engine_mod
    from precursor.backend.services.llm.base import TextDeltaEvent, TurnDoneEvent, UsageEvent

    class EchoUserPromptProvider:
        name = "echo"

        async def stream_chat(self, *, model, messages, reasoning_effort=None):
            yield ""

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
            last_user = next((m for m in reversed(messages) if m.role == "user"), None)
            yield TextDeltaEvent(content=last_user.content if last_user else "")
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            yield TurnDoneEvent(finish_reason="stop")

        async def list_models(self):
            return []

    async def _fake_get_llm_provider(_session):
        return EchoUserPromptProvider()

    async def _fake_record_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(conversation_turn_mod, "get_llm_provider", _fake_get_llm_provider)
    monkeypatch.setattr(turn_engine_mod, "record_usage", _fake_record_usage)

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/settings").json()["llm_max_attachment_chars"] == 200_000
        r = client.put("/api/settings", json={"llm_max_attachment_chars": 1_500})
        assert r.status_code == 200, r.text
        assert r.json()["llm_max_attachment_chars"] == 1_500
        try:
            tid = client.post("/api/topics", json={"title": "Cap"}).json()["id"]
            body = "b" * 1_400 + "END-OF-VISIBLE" + "c" * 3_000
            aid = client.post(
                f"/api/topics/{tid}/attachments",
                files={"file": ("long.txt", body.encode(), "text/plain")},
            ).json()["id"]
            stream = client.post(
                f"/api/topics/{tid}/messages/stream",
                json={"content": "read it", "attachment_ids": [aid]},
                headers={"Accept": "text/event-stream"},
            )
            assert stream.status_code == 200

            msgs = client.get(f"/api/topics/{tid}/messages").json()
            echoed = [m for m in msgs if m["role"] == "assistant"][-1]["content"]
            assert "b" * 1_400 in echoed
            assert "c" * 100 not in echoed
            assert f"showing the first 1,500 of {len(body):,} characters" in echoed
        finally:
            client.put("/api/settings", json={"llm_max_attachment_chars": 200_000})
