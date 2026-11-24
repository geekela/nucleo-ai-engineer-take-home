"""Client-side limits for uploads: at most N in flight and M started per rolling minute.

Keeping under the server's per-token limits (2 concurrent, 10/min) means extra
uploads wait here as "queued" instead of being refused with 429.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

# The server counts a rolling minute from when each upload arrives; we count from when we send.
# A small margin keeps the 11th upload from landing inside the server's window.
WINDOW_S = 62.0


class UploadLimiter:
    def __init__(
        self,
        max_concurrent: int = 2,
        per_minute: int = 10,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._started: deque[float] = deque()
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._sem:
            while True:
                now = self._clock()
                while self._started and now - self._started[0] >= WINDOW_S:
                    self._started.popleft()
                if len(self._started) < self._per_minute:
                    self._started.append(now)
                    break
                await self._sleep(self._started[0] + WINDOW_S - now)
            self._in_flight += 1
            try:
                yield
            finally:
                self._in_flight -= 1
