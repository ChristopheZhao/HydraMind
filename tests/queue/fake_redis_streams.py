"""Small async Redis Streams fake for queue adapter tests."""

from __future__ import annotations

from dataclasses import dataclass, field


class ResponseError(Exception):
    """Mirror of ``redis.exceptions.ResponseError`` (matched by type name).

    The adapter identifies redis server errors structurally so the optional
    redis dependency is never imported; the fake raises this class to stay
    faithful to redis-py wire behavior.
    """


@dataclass
class _PendingDelivery:
    consumer: str
    delivered_at_ms: int


@dataclass
class _GroupState:
    last_sequence: int = 0
    pending: dict[str, _PendingDelivery] = field(default_factory=dict)


class FakeRedisStreams:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[tuple[str, str], _GroupState] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.closed = False
        self._sequence = 0
        self._now_ms = 0

    def advance(self, seconds: float) -> None:
        self._now_ms += int(seconds * 1000)

    async def xgroup_create(
        self,
        *,
        name: str,
        groupname: str,
        id: str,
        mkstream: bool,
    ) -> None:
        del id
        key = (name, groupname)
        if key in self.groups:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        if mkstream:
            self.streams.setdefault(name, [])
        self.groups[key] = _GroupState()

    async def xadd(self, name: str, fields: dict[str, str]) -> str:
        self._sequence += 1
        entry_id = f"{self._sequence}-0"
        self.streams.setdefault(name, []).append((entry_id, dict(fields)))
        return entry_id

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        *,
        streams: dict[str, str],
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del count, block
        if not streams:
            return []
        name, marker = next(iter(streams.items()))
        if marker != ">":
            return []
        group = self.groups[(name, groupname)]
        for entry_id, fields in self.streams.get(name, []):
            sequence = _sequence(entry_id)
            if sequence <= group.last_sequence:
                continue
            group.last_sequence = sequence
            group.pending[entry_id] = _PendingDelivery(
                consumer=consumername,
                delivered_at_ms=self._now_ms,
            )
            return [(name, [(entry_id, dict(fields))])]
        return []

    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        *,
        count: int,
    ) -> tuple[str, list[tuple[str, dict[str, str]]], list[str]]:
        del start_id, count
        group = self.groups[(name, groupname)]
        for entry_id in sorted(group.pending, key=_sequence):
            pending = group.pending[entry_id]
            if self._now_ms - pending.delivered_at_ms < min_idle_time:
                continue
            pending.consumer = consumername
            pending.delivered_at_ms = self._now_ms
            fields = self._fields(name, entry_id)
            if fields is None:
                continue
            return ("0-0", [(entry_id, dict(fields))], [])
        return ("0-0", [], [])

    async def xack(self, name: str, groupname: str, *entry_ids: str) -> int:
        group = self.groups[(name, groupname)]
        removed = 0
        for entry_id in entry_ids:
            if group.pending.pop(entry_id, None) is not None:
                removed += 1
        return removed

    async def xdel(self, name: str, *entry_ids: str) -> int:
        entries = self.streams.get(name, [])
        before = len(entries)
        remove = set(entry_ids)
        self.streams[name] = [
            (entry_id, fields)
            for entry_id, fields in entries
            if entry_id not in remove
        ]
        return before - len(self.streams[name])

    async def xlen(self, name: str) -> int:
        return len(self.streams.get(name, []))

    async def xpending(self, name: str, groupname: str) -> dict[str, int]:
        group = self.groups[(name, groupname)]
        return {"pending": len(group.pending)}

    async def xrange(
        self,
        name: str,
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        entries = [
            (entry_id, dict(fields))
            for entry_id, fields in self.streams.get(name, [])
        ]
        if count is not None:
            return entries[:count]
        return entries

    async def hset(self, name: str, key: str, value: str) -> None:
        self.hashes.setdefault(name, {})[key] = value

    async def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    async def hdel(self, name: str, key: str) -> int:
        values = self.hashes.get(name)
        if values is None:
            return 0
        return 1 if values.pop(key, None) is not None else 0

    async def aclose(self) -> None:
        self.closed = True

    def _fields(self, name: str, entry_id: str) -> dict[str, str] | None:
        for current_id, fields in self.streams.get(name, []):
            if current_id == entry_id:
                return fields
        return None


def _sequence(entry_id: str) -> int:
    return int(entry_id.split("-", 1)[0])
