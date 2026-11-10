"""Azure AI Speech settings, as saved from the Settings → Speech tab.

The resource is reached with either its subscription key (``api_keys`` →
``azure_speech_key``) or the Azure CLI sign-in (``az login``), in the CLI's
default tenant unless one is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.schemas.settings import SpeechAuth
from precursor.backend.services.app_settings import (
    SettingSpec,
    _get_db_value,
    _nonempty_str,
    _str_in,
    resolve,
)

SPEECH_AUTH_MODES: tuple[str, ...] = get_args(SpeechAuth)


@dataclass(frozen=True)
class SpeechConfig:
    endpoint: str
    auth: SpeechAuth
    key: str
    tenant: str
    resource_id: str

    @property
    def ready(self) -> bool:
        """Whether there is enough to ask Azure for a token."""
        if not self.endpoint:
            return False
        return self.auth == "azure_cli" or bool(self.key)


async def resolve_azure_speech_key(session: AsyncSession) -> str:
    """Effective Azure Speech key from the DB ``api_keys`` (Settings panel)."""
    api_keys = await _get_db_value(session, "api_keys")
    if isinstance(api_keys, dict):
        key = api_keys.get("azure_speech_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    return ""


async def resolve_azure_speech_endpoint(session: AsyncSession) -> str:
    """Effective Azure Speech endpoint URL from the DB (Settings panel)."""
    return await resolve(session, SettingSpec("azure_speech_endpoint", _nonempty_str(), default=""))


async def resolve_azure_speech_language(session: AsyncSession) -> str:
    """Effective dictation language (BCP-47, e.g. ``en-US``); ``""`` => auto."""
    return await resolve(session, SettingSpec("azure_speech_language", _nonempty_str(), default=""))


async def resolve_azure_speech_auth(session: AsyncSession) -> SpeechAuth:
    """How the backend authenticates to Speech: the key or the Azure CLI."""
    value = await resolve(
        session, SettingSpec("azure_speech_auth", _str_in(SPEECH_AUTH_MODES), default="key")
    )
    return cast(SpeechAuth, value)


async def resolve_azure_speech_tenant(session: AsyncSession) -> str:
    """Tenant for the Azure CLI sign-in; ``""`` => the CLI's default tenant."""
    return await resolve(session, SettingSpec("azure_speech_tenant", _nonempty_str(), default=""))


async def resolve_azure_speech_resource_id(session: AsyncSession) -> str:
    """ARM id of the Speech resource; ``""`` => discovered from the endpoint."""
    return await resolve(
        session, SettingSpec("azure_speech_resource_id", _nonempty_str(), default="")
    )


async def resolve_speech_config(session: AsyncSession) -> SpeechConfig:
    """Everything needed to mint a Speech token."""
    return SpeechConfig(
        endpoint=await resolve_azure_speech_endpoint(session),
        auth=await resolve_azure_speech_auth(session),
        key=await resolve_azure_speech_key(session),
        tenant=await resolve_azure_speech_tenant(session),
        resource_id=await resolve_azure_speech_resource_id(session),
    )


async def azure_stt_ready(session: AsyncSession) -> bool:
    """True when the endpoint and the chosen credential are configured."""
    return (await resolve_speech_config(session)).ready
