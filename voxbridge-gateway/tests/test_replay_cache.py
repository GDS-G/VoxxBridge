from __future__ import annotations

import asyncio

import pytest

from voxbridge.replay_cache import ReplayCache, ReplayCacheCapacityError


async def test_replay_cache_coalesces_inflight_and_reuses_success() -> None:
    cache = ReplayCache[str](ttl_seconds=60, max_items=2)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def produce() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "audio-result"

    first = asyncio.create_task(cache.get_or_create("same", produce))
    await started.wait()
    second = asyncio.create_task(cache.get_or_create("same", produce))
    await asyncio.sleep(0)
    release.set()

    assert await first == "audio-result"
    assert await second == "audio-result"
    assert await cache.get_or_create("same", produce) == "audio-result"
    assert calls == 1
    assert cache.item_count == 1


async def test_replay_cache_does_not_cache_failures() -> None:
    cache = ReplayCache[str](ttl_seconds=60, max_items=2)
    calls = 0

    async def produce() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return "recovered"

    with pytest.raises(RuntimeError, match="temporary failure"):
        await cache.get_or_create("same", produce)

    assert await cache.get_or_create("same", produce) == "recovered"
    assert calls == 2


async def test_cancelled_waiter_does_not_cancel_shared_provider_work() -> None:
    cache = ReplayCache[str](ttl_seconds=60, max_items=2)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def produce() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "completed-after-disconnect"

    disconnected = asyncio.create_task(cache.get_or_create("same", produce))
    await started.wait()
    disconnected.cancel()
    with pytest.raises(asyncio.CancelledError):
        await disconnected

    release.set()
    assert await cache.get_or_create("same", produce) == "completed-after-disconnect"
    assert calls == 1


async def test_replay_cache_expires_and_evicts_oldest_completed_entry() -> None:
    now = 10.0
    cache = ReplayCache[str](ttl_seconds=5, max_items=2, clock=lambda: now)
    calls: dict[str, int] = {}

    async def get(key: str) -> str:
        async def produce() -> str:
            calls[key] = calls.get(key, 0) + 1
            return f"{key}-{calls[key]}"

        return await cache.get_or_create(key, produce)

    assert await get("a") == "a-1"
    assert await get("b") == "b-1"
    assert await get("c") == "c-1"
    assert cache.item_count == 2
    assert await get("a") == "a-2"

    now = 16.0
    assert cache.item_count == 0
    assert await get("a") == "a-3"


async def test_replay_cache_rejects_new_distinct_work_at_inflight_limit() -> None:
    cache = ReplayCache[str](ttl_seconds=60, max_items=2, max_inflight=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def produce() -> str:
        started.set()
        await release.wait()
        return "done"

    active = asyncio.create_task(cache.get_or_create("active", produce))
    await started.wait()

    with pytest.raises(ReplayCacheCapacityError, match="maximum concurrent"):
        await cache.get_or_create("different", produce)

    duplicate = asyncio.create_task(cache.get_or_create("active", produce))
    release.set()
    assert await active == "done"
    assert await duplicate == "done"
