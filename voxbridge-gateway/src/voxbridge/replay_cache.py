from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class ReplayCacheCapacityError(RuntimeError):
    """Raised before starting new work when the in-flight limit is full."""


@dataclass(slots=True)
class _Completed(Generic[T]):
    value: T
    expires_at: float


class ReplayCache(Generic[T]):
    """A small TTL cache that coalesces identical in-flight work.

    The producer runs in its own task so cancellation of one caller does not
    cancel a provider request that may already be billable. Failed or cancelled
    producers are never cached.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_items: int,
        max_inflight: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        if max_inflight is not None and max_inflight <= 0:
            raise ValueError("max_inflight must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._max_inflight = max_items if max_inflight is None else max_inflight
        self._clock = clock
        self._completed: OrderedDict[str, _Completed[T]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[T]] = {}
        self._lock = asyncio.Lock()

    @property
    def item_count(self) -> int:
        self._prune(self._clock())
        return len(self._completed)

    def clear(self) -> None:
        """Discard completed entries and stop any in-flight cache workers."""

        self._completed.clear()
        tasks = tuple(self._inflight.values())
        self._inflight.clear()
        for task in tasks:
            task.cancel()

    async def get_or_create(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
    ) -> T:
        now = self._clock()
        async with self._lock:
            self._prune(now)
            completed = self._completed.get(key)
            if completed is not None:
                self._completed.move_to_end(key)
                return completed.value

            task = self._inflight.get(key)
            if task is None:
                if len(self._inflight) >= self._max_inflight:
                    raise ReplayCacheCapacityError(
                        "maximum concurrent replay-protected work is already in progress"
                    )
                task = asyncio.create_task(self._produce(key, factory))
                task.add_done_callback(self._consume_background_exception)
                self._inflight[key] = task

        # A disconnected or cancelled host request must not cancel a provider
        # request shared with an approval replay or retry.
        return await asyncio.shield(task)

    async def _produce(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
    ) -> T:
        current_task = asyncio.current_task()
        try:
            value = await factory()
        except BaseException:
            async with self._lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)
            raise

        async with self._lock:
            if self._inflight.get(key) is current_task:
                self._inflight.pop(key, None)
                now = self._clock()
                self._prune(now)
                self._completed[key] = _Completed(
                    value=value,
                    expires_at=now + self._ttl_seconds,
                )
                self._completed.move_to_end(key)
                while len(self._completed) > self._max_items:
                    self._completed.popitem(last=False)
        return value

    @staticmethod
    def _consume_background_exception(task: asyncio.Task[T]) -> None:
        """Mark a detached producer exception observed after its waiter disconnects."""

        if task.cancelled():
            return
        task.exception()

    def _prune(self, now: float) -> None:
        expired = [key for key, completed in self._completed.items() if completed.expires_at <= now]
        for key in expired:
            self._completed.pop(key, None)
