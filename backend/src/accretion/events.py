from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from accretion.models import EventEnvelope


class EventBroker:
    def __init__(self, *, queue_size: int = 500) -> None:
        self._sequence = 0
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[EventEnvelope]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, data: dict[str, Any]) -> EventEnvelope:
        async with self._lock:
            self._sequence += 1
            envelope = EventEnvelope(sequence=self._sequence, type=event_type, data=data)
            stale: list[asyncio.Queue[EventEnvelope]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    stale.append(queue)
            for queue in stale:
                self._subscribers.discard(queue)
            return envelope

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[EventEnvelope]]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
