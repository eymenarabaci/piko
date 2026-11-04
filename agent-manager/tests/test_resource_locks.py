import asyncio

import pytest

from manager.resource_locks import LockTimeout, LeaseLost, ResourceLocks


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


async def test_renewal_preserves_ownership_beyond_original_expiry(locks, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from manager import resource_locks
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(resource_locks, "_now", lambda: clock[0])
    lease = await locks.acquire("project:long", "run-a", timeout=0, ttl=60)
    clock[0] += timedelta(seconds=40)
    await lease.renew(60)
    clock[0] += timedelta(seconds=40)
    await lease.assert_owned()
    with pytest.raises(LockTimeout):
        await locks.acquire("project:long", "run-b", timeout=0, ttl=60)
    await lease.release()


async def test_live_same_run_cannot_steal_but_dead_owner_can_be_recovered(tmp_path, monkeypatch):
    """A restarted driver takes its own workspace back at once.

    Crashing mid-run used to park the job for the whole lease TTL because the
    dead process left its token behind. Cross-process double-driving is closed
    by the single-instance driver lock instead, so the run itself is allowed to
    reclaim — and the abandoned token is invalidated so it can never renew.
    """
    one, two = ResourceLocks(tmp_path / "locks.sqlite"), ResourceLocks(tmp_path / "locks.sqlite")
    await one.open(); await two.open()
    try:
        stale = await one.acquire("workspace:a", "same-run", timeout=0, ttl=60)
        with pytest.raises(LockTimeout):
            await two.acquire("workspace:a", "same-run", timeout=0, ttl=60)
        await stale.assert_owned()
        monkeypatch.setattr('manager.resource_locks._owner_alive', lambda _: False)
        reclaimed = await two.acquire("workspace:a", "same-run", timeout=0, ttl=60)
        assert (await two.holder("workspace:a"))["token"] == reclaimed.token
        with pytest.raises(LeaseLost):
            await stale.assert_owned()
        with pytest.raises(LeaseLost):
            await stale.renew(60)
        await reclaimed.release()
        assert await two.holder("workspace:a") is None
    finally:
        await one.close(); await two.close()


async def test_stale_release_cannot_delete_replacement_with_same_run_id(locks, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from manager import resource_locks
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(resource_locks, "_now", lambda: clock[0])
    old = await locks.acquire("workspace:a", "same-run", timeout=0, ttl=10)
    clock[0] += timedelta(seconds=11)
    new = await locks.acquire("workspace:a", "same-run", timeout=0, ttl=60)
    assert old.token != new.token
    with pytest.raises(LeaseLost):
        await old.renew(60)
    await old.release()
    await new.assert_owned()
    await new.release()
