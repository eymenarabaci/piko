"""Phase 3 acceptance: a waiting decision must not stall the queue.

Scenario
--------
* **A** — ``waiting``: a user decision is outstanding, so it must be skipped.
* **B** — ``open``: its workspace is held by an external lease (slow resource).
* **C** — ``open``: a fast office job in its own workspace.

Acceptance:
1. One tick starts B and C even though A is waiting (the slot does not rot).
2. C finishes while A is still waiting and before B gets its lock.
3. B takes the lock over once its TTL expires and finishes.
4. Observed concurrency never exceeds ``max_concurrent_runs`` (2).
5. Slot occupancy and queue-wait counters are recorded for the developer screen.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import pytest

from manager.bridge_gate import BridgeGate
from manager.daemon import DriverDaemon
from manager.driver_hooks import EngineDriverHooks
from manager.engine import Engine
from manager.metrics import MetricsStore
from manager.notifications import NotificationManager
from manager.operations import OperationLedger
from manager.resource_locks import ResourceLocks
from manager.store import Store
from manager.work_record import WorkRecordManager


class TimedProviders:
    models = [{"id": "worker", "provider": "test", "execution_supported": True}]

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.prompts: list[str] = []
        self.finished: list[str] = []

    def available(self):
        return [{"id": "test", "available": True, "executable": "test-only"}]

    async def run(self, model_id, prompt, *, workspace, allow_writes, timeout,
                  on_event, effort=""):
        self.prompts.append(prompt)
        delays = {"A:": 0.02, "B:": 1.2, "C:": 0.6}
        delay = next((value for key, value in delays.items() if key in prompt), 0.02)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(delay)
            label = next((key for key in delays if key in prompt), "?")
            self.finished.append(label)
            return {"output": f"tamam {label}", "model_id": model_id,
                    "status": "completed", "usage": {}}
        finally:
            self.active -= 1


@asynccontextmanager
async def stack(tmp_path):
    store = Store(tmp_path / "runs.sqlite")
    await store.open()
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    notifications = NotificationManager(tmp_path / "notifications.sqlite")
    await notifications.open()
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    metrics = MetricsStore(tmp_path / "metrics.sqlite")
    await metrics.open()
    locks = ResourceLocks(tmp_path / "locks.sqlite")
    await locks.open()
    providers = TimedProviders()
    try:
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
            engine = Engine(store, saver, providers, tmp_path)
            engine.attach_infrastructure(
                work=work, notifications=notifications, ledger=ledger,
                metrics=metrics, resource_locks=locks,
                bridge_gate=BridgeGate(token_store=ledger))
            engine.resource_lock_timeout = 0.05
            engine.resource_retry_seconds = 0.1
            engine.set_max_concurrent_runs(2)
            try:
                yield engine, work, notifications, metrics, locks, providers
            finally:
                await engine.close()
    finally:
        await locks.close()
        await metrics.close()
        await ledger.close()
        await notifications.close()
        await work.close()
        await store.close()


def _key(path):
    return f"workspace:{os.path.normcase(str(path.resolve()))}"


async def _wait_for(predicate, *, timeout=8.0, interval=0.05):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def test_waiting_does_not_block_queue_and_lock_ttl_is_taken_over(tmp_path):
    workspace_b = tmp_path / "ws-b"
    workspace_c = tmp_path / "ws-c"
    workspace_b.mkdir()
    workspace_c.mkdir()

    async with stack(tmp_path) as (engine, work, _notes, metrics, locks, providers):
        item_a = await work.create("A: karar bekleyen iş", title="A")
        await work.add_decision(item_a["id"], "A devam etsin mi?")
        waiting_a = await work.get(item_a["id"])
        assert waiting_a["status"] == "waiting"

        item_b = await work.create("B: yavaş kaynak", title="B",
                                   workspace=str(workspace_b))
        item_c = await work.create("C: hızlı ofis işi", title="C",
                                   workspace=str(workspace_c))

        # A slow external holder owns B's workspace until the TTL expires.
        holder = await locks.acquire(_key(workspace_b), "external-holder",
                                     timeout=0, ttl=0.35)
        try:
            hooks = EngineDriverHooks(engine, default_mode="chat", executor_model="worker")
            daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite",
                                  ledger=engine.ledger, tick_seconds=1,
                                  reminder_seconds=(1, 4, 24), block_after_seconds=72)
            await daemon.open()
            try:
                stats = await daemon.tick()
                # A is skipped; B and C are both picked up in the same tick.
                assert stats["started"] == 2, stats
                assert stats["skipped"] == 0, stats

                # No run is ever created for the waiting decision.
                assert await work.get(item_a["id"]) is not None
                assert (await work.get(item_a["id"]))["run_id"] == ""

                run_c = next(item for item in await engine.store.list()
                             if item["prompt"].startswith("C:"))
                run_b = next(item for item in await engine.store.list()
                             if item["prompt"].startswith("B:"))

                # C completes while A still waits and before B can run.
                assert await _wait_for(
                    lambda: _status(engine, run_c["id"], "completed")), "C bitmedi"
                a_now = await work.get(item_a["id"])
                assert a_now["status"] == "waiting", "A beklemesi bozulmamalı"
                b_now = await engine.store.get(run_b["id"])
                assert b_now["status"] != "completed", "B, kilit devralınmadan bitmemeli"

                # The lock is still held externally at that point.
                assert await locks.holder(_key(workspace_b)) is not None
            finally:
                await daemon.close()
        finally:
            await holder.release()

        # After the TTL, the retry loop takes the lock over and finishes B.
        assert await _wait_for(lambda: _status(engine, run_b["id"], "completed"),
                               timeout=10.0), "B kilidi devralmadı"
        events = await engine.store.events(run_b["id"])
        kinds = {event["kind"] for event in events["events"]}
        assert "resource_wait" in kinds, "kilit beklemesi olay olarak kaydedilmeli"
        assert await _wait_for(lambda: _slot_idle(engine), timeout=5.0)

        # Concurrency ceiling was reached but never exceeded.
        assert providers.max_active == 2, providers.max_active
        assert engine.slot_max_observed <= engine.max_concurrent_runs == 2
        snapshot = engine.slot_snapshot()
        assert snapshot["slot_active"] == 0
        assert snapshot["slot_wait_count"] >= 2
        assert snapshot["slot_wait_seconds_total"] >= 0

        runtime = await metrics.runtime()
        assert runtime["gauges"]["max_concurrent_runs"] == 2
        assert runtime["gauges"]["slot_max_observed"] == 2
        assert runtime["counters"]["slot_wait_count"] >= 2
        assert runtime["slot_wait_avg_seconds"] >= 0

        # A still owns its decision and never started a run.
        assert (await work.get(item_a["id"]))["status"] == "waiting"
        assert len([run for run in await engine.store.list()
                    if run["prompt"].startswith("A:")]) == 0


async def test_reminders_follow_one_four_twenty_four_rule(tmp_path):
    async with stack(tmp_path) as (engine, work, notifications, _metrics, _locks, _providers):
        item = await work.create("Hatırlatma işi", title="Hatırlatma")
        await work.add_decision(item["id"], "Devam edeyim mi?")
        base = datetime.now(timezone.utc)
        hooks = EngineDriverHooks(engine, default_mode="chat", executor_model="worker")
        daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite",
                              ledger=engine.ledger, tick_seconds=1,
                              reminder_seconds=(1, 4, 24), block_after_seconds=72)
        await daemon.open()
        try:
            def reminders(notes):
                return [note for note in notes if "Karar" in note["title"]]

            # 0 s: nothing yet.
            await daemon.tick(now=base)
            assert reminders(await notifications.list()) == []

            # 1 s: first reminder.
            await daemon.tick(now=base + timedelta(seconds=2))
            assert len(reminders(await notifications.list())) == 1

            # 4 s: second reminder (distinct dedupe key).
            await daemon.tick(now=base + timedelta(seconds=5))
            assert len(reminders(await notifications.list())) == 2

            # 24 s: third reminder.
            await daemon.tick(now=base + timedelta(seconds=25))
            assert len(reminders(await notifications.list())) == 3

            # Re-ticking the same instants never duplicates a reminder.
            await daemon.tick(now=base + timedelta(seconds=25))
            assert len(reminders(await notifications.list())) == 3

            # 72 s: the decision window expires and the work is blocked once.
            await daemon.tick(now=base + timedelta(seconds=80))
            blocked = await work.get(item["id"])
            assert blocked["status"] == "blocked"
            blockers = [note for note in await notifications.list()
                        if note["kind"] == "blocker"]
            assert len(blockers) == 1
        finally:
            await daemon.close()


async def _status(engine, run_id, expected):
    run = await engine.store.get(run_id)
    return bool(run and run.get("status") == expected)


async def _slot_idle(engine):
    return engine.slot_active == 0 and engine.slot_waiters == 0
