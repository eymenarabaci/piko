import asyncio

import pytest

from manager.resource_locks import LockTimeout, ResourceLocks


@pytest.fixture
async def locks(tmp_path):
    store = ResourceLocks(tmp_path / "lock.db")
    await store.open()
    try:
        yield store
    finally:
        await store.close()


async def test_second_acquirer_blocks_then_times_out(locks):
    first = await locks.acquire("kicad:proje-x", "run-a", timeout=0, ttl=60)
    with pytest.raises(LockTimeout):
        await locks.acquire("kicad:proje-x", "run-b", timeout=0.15, ttl=60)
    assert (await locks.holder("kicad:proje-x"))["run_id"] == "run-a"
    await first.release()
    assert await locks.holder("kicad:proje-x") is None


async def test_ttl_expiry_allows_takeover(locks):
    stale = await locks.acquire("device:scope", "run-dead", timeout=0, ttl=0.05)
    await asyncio.sleep(0.12)
    takeover = await locks.acquire("device:scope", "run-alive", timeout=0, ttl=60)
    assert (await locks.holder("device:scope"))["run_id"] == "run-alive"
    await stale.release()
    await takeover.release()


async def test_stale_cleanup_removes_expired_rows(locks):
    await locks.acquire("file:rapor.docx", "run-dead", timeout=0, ttl=0.05)
    await asyncio.sleep(0.12)
    removed = await locks.temizle("another-run")
    assert removed >= 1
    assert await locks.holder("file:rapor.docx") is None


async def test_two_concurrent_tasks_race_for_one_resource(locks):
    results = await asyncio.gather(
        locks.acquire("project:alpha", "run-1", timeout=0, ttl=60),
        locks.acquire("project:alpha", "run-2", timeout=0, ttl=60),
        return_exceptions=True,
    )
    wins = [item for item in results if not isinstance(item, Exception)]
    losses = [item for item in results if isinstance(item, LockTimeout)]
    assert len(wins) == 1
    assert len(losses) == 1
    assert (await locks.holder("project:alpha"))["run_id"] == wins[0].run_id
    await wins[0].release()


async def test_same_run_may_reacquire_same_resource(locks):
    async with await locks.acquire("kicad:proje-x", "run-a", timeout=0, ttl=60):
        async with await locks.acquire("kicad:proje-x", "run-a", timeout=0, ttl=60):
            assert (await locks.holder("kicad:proje-x"))["run_id"] == "run-a"
        # İç kiralama bırakıldı; dış sahiplik hâlâ ayakta olmalı.
        assert (await locks.holder("kicad:proje-x"))["run_id"] == "run-a"
    assert await locks.holder("kicad:proje-x") is None


async def test_release_hands_off_immediately(locks):
    lease = await locks.acquire("kicad:proje-x", "run-a", timeout=0, ttl=60)
    await lease.release()
    next_lease = await locks.acquire("kicad:proje-x", "run-b", timeout=0, ttl=60)
    assert (await locks.holder("kicad:proje-x"))["run_id"] == "run-b"
    await next_lease.release()


async def test_different_resources_do_not_conflict(locks):
    one = await locks.acquire("kicad:proje-x", "run-a", timeout=0, ttl=60)
    two = await locks.acquire("kicad:proje-y", "run-b", timeout=0, ttl=60)
    assert (await locks.holder("kicad:proje-x"))["run_id"] == "run-a"
    assert (await locks.holder("kicad:proje-y"))["run_id"] == "run-b"
    await one.release()
    await two.release()
