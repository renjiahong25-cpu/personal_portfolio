"""A small async throttle shared by the search and download entry points.

Music platforms answer abuse with risk control (Bilibili returns HTTP 412
within seconds of repeated searching), so *every* outbound search has to be
spaced out, not just downloads.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class MinIntervalThrottle:
    """Guarantee at least ``min_interval`` seconds between two operations."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = max(0.0, min_interval)
        self._clock = clock
        self._last_finished: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> float:
        """Block until the previous operation is old enough. Returns waited seconds."""
        async with self._lock:
            waited = 0.0
            if self._last_finished is not None and self._min_interval > 0:
                elapsed = self._clock() - self._last_finished
                waited = max(0.0, self._min_interval - elapsed)
                if waited > 0:
                    await asyncio.sleep(waited)
            # Stamp the finish time *after* sleeping, otherwise the next caller
            # would be asked to wait for the wait as well.
            self._last_finished = self._clock()
            return waited


__all__ = ["MinIntervalThrottle"]
