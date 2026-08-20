from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from accretion.contracts import Provider


class ConcurrencyLimiter:
    def __init__(self, *, global_limit: int, provider_limit: int, project_limit: int) -> None:
        self.global_semaphore = asyncio.Semaphore(global_limit)
        self.provider_limit = provider_limit
        self.project_limit = project_limit
        self.provider_semaphores: defaultdict[Provider, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.provider_limit)
        )
        self.project_semaphores: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.project_limit)
        )

    @asynccontextmanager
    async def slot(self, provider: Provider, project_id: str) -> AsyncIterator[None]:
        async with (
            self.global_semaphore,
            self.provider_semaphores[provider],
            self.project_semaphores[project_id],
        ):
            yield
