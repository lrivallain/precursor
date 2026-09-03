"""The chat model is reconciled with what the provider actually offers.

A pinned factory default is only correct until the provider retires that id,
after which every turn fails — which is exactly what happened to the previous
``claude-sonnet-4.5`` default. These tests pin the replacement behaviour: the
stored choice wins while it exists, a retired one degrades to a working model
instead of erroring, and an unreachable catalogue never overrides the user.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting
from precursor.backend.services import model_catalog
from precursor.backend.services.app_settings import resolve_llm_model
from precursor.backend.services.llm.base import LLMModel


def _catalog(*ids: str) -> list[LLMModel]:
    return [LLMModel(id=i, name=i, publisher="test", summary="") for i in ids]


class _Provider:
    """Minimal provider stub that records how often its catalogue is read."""

    name = "stub"

    def __init__(self, models: list[LLMModel] | Exception) -> None:
        self._models = models
        self.calls = 0

    async def list_models(self) -> list[LLMModel]:
        self.calls += 1
        if isinstance(self._models, Exception):
            raise self._models
        return self._models


@pytest.fixture(autouse=True)
def _clean_catalog_cache():
    model_catalog.invalidate_model_catalog()
    yield
    model_catalog.invalidate_model_catalog()


@pytest.fixture(autouse=True)
async def _clean_stored_model():
    """Other modules assert on default settings, so don't leak a stored id."""
    with TestClient(create_app()):
        pass
    yield
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "llm_model")
        if row is not None:
            await session.delete(row)
            await session.commit()


def _use(monkeypatch: pytest.MonkeyPatch, provider: _Provider) -> None:
    async def _get(_session, **_kw):  # type: ignore[no-untyped-def]
        return provider

    monkeypatch.setattr("precursor.backend.services.llm.get_llm_provider", _get, raising=True)


async def _store(value: str) -> None:
    async with SessionLocal() as session:
        session.add(AppSetting(key="llm_model", value=f'"{value}"'))
        await session.commit()


async def test_unset_model_uses_the_first_model_the_provider_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _Provider(_catalog("gpt-5-mini", "claude-opus-5")))
    async with SessionLocal() as session:
        assert await resolve_llm_model(session) == "gpt-5-mini"


async def test_a_still_offered_choice_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, _Provider(_catalog("gpt-5-mini", "claude-opus-5")))
    await _store("claude-opus-5")
    async with SessionLocal() as session:
        assert await resolve_llm_model(session) == "claude-opus-5"


async def test_a_retired_choice_degrades_instead_of_failing_every_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _Provider(_catalog("gpt-5-mini", "claude-opus-5")))
    await _store("claude-sonnet-4.5")
    async with SessionLocal() as session:
        assert await resolve_llm_model(session) == "gpt-5-mini"


async def test_an_unreachable_catalogue_never_overrides_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, _Provider(RuntimeError("network down")))
    await _store("claude-opus-5")
    async with SessionLocal() as session:
        assert await resolve_llm_model(session) == "claude-opus-5"


async def test_the_catalogue_is_fetched_once_and_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(_catalog("gpt-5-mini"))
    _use(monkeypatch, provider)
    async with SessionLocal() as session:
        for _ in range(3):
            await resolve_llm_model(session)
    assert provider.calls == 1, "the catalogue must not be re-fetched per turn"


async def test_invalidation_forces_a_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _Provider(_catalog("gpt-5-mini"))
    _use(monkeypatch, provider)
    async with SessionLocal() as session:
        await resolve_llm_model(session)
        model_catalog.invalidate_model_catalog()
        await resolve_llm_model(session)
    assert provider.calls == 2, "adding a credential must not serve a stale catalogue"


async def test_no_model_id_is_pinned_as_a_factory_default() -> None:
    """The bug this replaced: a literal that the provider later retires."""
    from precursor.backend.schemas.settings import SettingsRead
    from precursor.backend.services.app_settings import DEFAULT_LLM_MODEL

    assert DEFAULT_LLM_MODEL == ""
    assert SettingsRead.model_fields["llm_model"].default == ""
