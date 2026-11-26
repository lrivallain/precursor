"""Signing out every paired device at once."""

from __future__ import annotations

from precursor.backend.services.remote_access import sessions as store


async def sign_out_all_devices() -> None:
    """Revoke every tunnel session and rotate the secrets a phone could have copied.

    A paired phone is trusted like the computer (ADR-0002): it could read the
    OpenAI-compatible endpoint's key through the app's own tools, and that
    endpoint doesn't need pairing. Rotating the key on a full sign-out means a
    copy can't outlive the devices. Clients of the endpoint need the new key.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.app_settings import resolve_openai_proxy_key
    from precursor.backend.services.openai_proxy import rotate_proxy_key

    await store.update(store.revoke)
    async with SessionLocal() as db:
        if await resolve_openai_proxy_key(db):
            await rotate_proxy_key(db)
            await db.commit()
