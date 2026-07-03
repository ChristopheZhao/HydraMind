"""Control-local persistence adapter for RuntimeSession storage."""

from __future__ import annotations

from collections.abc import Callable

from hydramind.control.models import RuntimeSession
from hydramind.control.store import SessionStore

MissingSessionFactory = Callable[[str], Exception]


class SessionRepository:
    """Small adapter around SessionStore read/write operations."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    async def get_required(
        self,
        session_id: str,
        *,
        missing: MissingSessionFactory,
    ) -> RuntimeSession:
        session = await self._store.get(session_id)
        if session is None:
            raise missing(session_id)
        return session

    async def put(self, session: RuntimeSession) -> None:
        await self._store.put(session)


__all__ = ["SessionRepository"]
