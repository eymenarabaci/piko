"""Shared workspace leases: real SQLite clients and cross-process conflicts."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys

import pytest

from manager import resource_locks
from manager.resource_locks import LeaseLost, LockTimeout, ResourceLocks


@pytest.fixture
async def stores(tmp_path):
    clients = [ResourceLocks(tmp_path / "locks.sqlite") for _ in range(3)]
    for client in clients:
        await client.open()
    try:
        yield clients
    finally:
        for client in clients:
            await client.close()


async def test_independent_readers_coexist_and_writer_waits_for_last_reader(stores):
    one, two, writer = stores
    a = await one.acquire("workspace:x", "reader-a", shared=True, timeout=0)
    b = await two.acquire("workspace:x", "reader-b", shared=True, timeout=0)
    assert (await one.holder("workspace:x"))["shared"] is True
    for reader in (a, b):
        await reader.assert_owned()
    with pytest.raises(LockTimeout):
        await writer.acquire("workspace:x", "writer", timeout=0)
    await a.release()
    await b.assert_owned()
    with pytest.raises(LockTimeout):
        await writer.acquire("workspace:x", "writer", timeout=0)
    await b.release()
    lease = await writer.acquire("workspace:x", "writer", timeout=0)
    assert (await writer.holder("workspace:x"))["run_id"] == "writer"
    assert (await writer.holder("workspace:x"))["shared"] is False
    await lease.release()


@pytest.mark.parametrize("held_shared,wanted_shared,allowed", [
    (True, True, True), (True, False, False),
    (False, True, False), (False, False, False),
])
async def test_separate_process_conflict_matrix(tmp_path, held_shared, wanted_shared, allowed):
    database = tmp_path / "process-locks.sqlite"
    # The child only holds the test SQLite lease; it never invokes a provider.
    script = """
import asyncio,sys
from manager.resource_locks import ResourceLocks
async def main():
    store=ResourceLocks(sys.argv[1])
    await store.open()
    try:
        lease=await store.acquire('workspace:x','child',shared=sys.argv[2]=='True',timeout=0)
        print('held',flush=True)
        try:
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            await lease.release()
    finally:
        await store.close()
asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-B", "-c", script, str(database), str(held_shared),
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    parent = ResourceLocks(database)
    opened = False
    try:
        assert (await asyncio.wait_for(child.stdout.readline(), 10)).strip() == b"held"
        await parent.open()
        opened = True
        if allowed:
            lease = await parent.acquire("workspace:x", "parent", shared=wanted_shared, timeout=0)
            await lease.assert_owned()
            await lease.release()
        else:
            with pytest.raises(LockTimeout):
                await parent.acquire("workspace:x", "parent", shared=wanted_shared, timeout=0)
        # Parent failure must not alter the child's token/expiry.
        assert (await parent.holder("workspace:x"))["run_id"] == "child"
    finally:
        if opened:
            await parent.close()
        if child.returncode is None:
            child.stdin.write(b"release\n")
            try:
                await child.stdin.drain()
                await asyncio.wait_for(child.wait(), 5)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                if child.returncode is None:
                    child.kill()
                await child.wait()
        if child.stdin:
            child.stdin.close()
    assert child.returncode == 0, (await child.stderr.read()).decode(errors="replace")


async def test_reader_renewal_expiry_and_old_token_release(stores, monkeypatch):
    clock = [datetime(2026, 11, 4, tzinfo=timezone.utc)]
    monkeypatch.setattr(resource_locks, "_now", lambda: clock[0])
    one, two, writer = stores
    old = await one.acquire("workspace:x", "reader", shared=True, timeout=0, ttl=10)
    other = await two.acquire("workspace:x", "other", shared=True, timeout=0, ttl=100)
    clock[0] += timedelta(seconds=8)
    await old.renew(10)
    clock[0] += timedelta(seconds=5)
    await old.assert_owned()
    clock[0] += timedelta(seconds=6)
    with pytest.raises(LeaseLost):
        await old.assert_owned()
    new = await one.acquire("workspace:x", "reader", shared=True, timeout=0, ttl=60)
    assert old.token != new.token
    with pytest.raises(LeaseLost):
        await old.renew(60)
    await old.release()
    await new.assert_owned()
    await other.assert_owned()
    with pytest.raises(LockTimeout):
        await writer.acquire("workspace:x", "writer", timeout=0)
    await new.release()
    await other.release()


async def test_shared_reentrancy_cleanup_does_not_release_peer(stores):
    one, two, writer = stores
    a = await one.acquire("workspace:x", "reader", shared=True, timeout=0)
    nested = await one.acquire("workspace:x", "reader", shared=True, timeout=0)
    peer = await two.acquire("workspace:x", "peer", shared=True, timeout=0)
    assert a.token == nested.token
    await nested.release()
    await a.assert_owned()
    assert await one.temizle("reader") == 1
    with pytest.raises(LeaseLost):
        await a.assert_owned()
    await peer.assert_owned()
    with pytest.raises(LockTimeout):
        await writer.acquire("workspace:x", "writer", timeout=0)
    await peer.release()


async def test_writer_reentry_stays_exclusive_and_reader_upgrade_is_not_silent(stores):
    one, two, _ = stores
    writer = await one.acquire("workspace:x", "owner", timeout=0)
    nested = await one.acquire("workspace:x", "owner", shared=True, timeout=0)
    assert writer.token == nested.token
    assert (await one.holder("workspace:x"))["shared"] is False
    with pytest.raises(LockTimeout):
        await two.acquire("workspace:x", "peer", shared=True, timeout=0)
    await nested.release()
    await writer.release()
    reader = await one.acquire("workspace:x", "owner", shared=True, timeout=0)
    with pytest.raises(LockTimeout):
        await one.acquire("workspace:x", "owner", timeout=0)
    await reader.assert_owned()
    await reader.release()


@pytest.mark.parametrize("with_token", [False, True])
async def test_legacy_writer_migration_preserves_owner_and_token(tmp_path, with_token):
    database = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE resource_locks(resource TEXT PRIMARY KEY,run_id TEXT NOT NULL,"
                       "acquired_at TEXT NOT NULL,expires_at TEXT NOT NULL"
                       + (",token TEXT NOT NULL DEFAULT ''" if with_token else "") + ")")
    moment = datetime.now(timezone.utc)
    values = ["workspace:x", "legacy", moment.isoformat(), (moment + timedelta(minutes=5)).isoformat()]
    if with_token:
        values.append("legacy-token")
    connection.execute("INSERT INTO resource_locks VALUES(" + ",".join("?" for _ in values) + ")", values)
    connection.commit()
    connection.close()
    client = ResourceLocks(database)
    await client.open()
    try:
        holder = await client.holder("workspace:x")
        assert holder["run_id"] == "legacy" and holder["shared"] is False
        assert holder["token"] == ("legacy-token" if with_token else "")
        with pytest.raises(LockTimeout):
            await client.acquire("workspace:x", "reader", shared=True, timeout=0)
        with pytest.raises(LockTimeout):
            await client.acquire("workspace:x", "writer", timeout=0)
    finally:
        await client.close()


async def test_simultaneous_writers_on_independent_connections_have_one_winner(stores):
    attempts = await asyncio.gather(*(client.acquire("workspace:x", f"writer-{i}", timeout=0)
                                      for i, client in enumerate(stores)), return_exceptions=True)
    wins = [item for item in attempts if not isinstance(item, BaseException)]
    assert len(wins) == 1
    assert sum(isinstance(item, LockTimeout) for item in attempts) == 2
    await wins[0].release()


async def test_simultaneous_reader_writer_acquisition_never_mixes_modes(stores):
    modes = [True, True, False]
    outcomes = await asyncio.gather(*(client.acquire("workspace:race", f"run-{i}", shared=mode, timeout=0)
                                      for i, (client, mode) in enumerate(zip(stores, modes))),
                                    return_exceptions=True)
    winners = [(mode, lease) for mode, lease in zip(modes, outcomes)
               if not isinstance(lease, BaseException)]
    assert winners
    assert all(mode for mode, _ in winners) or (len(winners) == 1 and not winners[0][0])
    assert all(isinstance(value, LockTimeout) for value in outcomes if isinstance(value, BaseException))
    for _, lease in winners:
        await lease.assert_owned()
        await lease.release()
