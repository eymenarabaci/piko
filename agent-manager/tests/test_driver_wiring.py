"""Wiring tests: driver hooks, daemon ticks, bridge gate + ledger, resource locks."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import pytest

from manager.bridge_gate import ApprovalRequired, BridgeGate
from manager.daemon import DriverDaemon
from manager.driver_hooks import EngineDriverHooks
from manager.engine import Engine
from manager.notifications import NotificationManager
from manager.operations import OperationLedger
from manager.resource_locks import ResourceLocks
from manager.store import Store
from manager.work_record import WorkRecordManager


class ChatProviders:
    models = [{"id": "worker", "provider": "test", "execution_supported": True}]

    def __init__(self):
        self.calls = 0

    def available(self):
        return [{"id": "test", "available": True, "executable": "test-only"}]

    async def run(self, model_id, prompt, *, workspace, allow_writes, timeout, on_event, effort=""):
        self.calls += 1
        return {"output": "tamam", "model_id": model_id, "status": "completed", "usage": {}}


@asynccontextmanager
async def stack(tmp_path, *, locks=False):
    store = Store(tmp_path / "runs.sqlite")
    await store.open()
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    notifications = NotificationManager(tmp_path / "notifications.sqlite")
    await notifications.open()
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    locks = None
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        engine = Engine(store, saver, ChatProviders(), tmp_path)
        resource_locks = ResourceLocks(tmp_path / "locks.sqlite")
        await resource_locks.open()
        engine.attach_infrastructure(work=work, notifications=notifications, ledger=ledger,
                                     resource_locks=resource_locks,
                                     bridge_gate=BridgeGate(token_store=ledger))
        try:
            yield engine, work, notifications, ledger, resource_locks
        finally:
            await engine.close()
            await resource_locks.close()
            await ledger.close()
            await notifications.close()
            await work.close()
    await store.close()


async def settle(engine, run_id):
    task = engine.tasks.get(run_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 10)
    return await engine.store.get(run_id)


async def test_driver_start_work_creates_exactly_one_run(tmp_path):
    async with stack(tmp_path) as (engine, work, _notes, _ledger, _locks):
        item = await work.create("Kısa rapor yaz", title="Rapor")
        hooks = EngineDriverHooks(engine, default_mode="chat", executor_model="worker")
        first = await hooks.start_work(dict(item))
        assert first
        second = await hooks.start_work(await work.get(item["id"]))
        assert second == first, "ikinci tick yeni run açmamalı"
        await settle(engine, first)
        linked = await work.by_run(first)
        assert linked is not None and linked["id"] == item["id"]


async def test_driver_resume_does_not_start_finished_run(tmp_path):
    async with stack(tmp_path) as (engine, work, _notes, _ledger, _locks):
        item = await work.create("Kısa rapor yaz", title="Rapor")
        hooks = EngineDriverHooks(engine, default_mode="chat", executor_model="worker")
        run_id = await hooks.start_work(dict(item))
        await settle(engine, run_id)
        created = await hooks.start_work(await work.get(item["id"]))
        assert created == run_id
        resumed = await hooks.resume_work(await work.get(item["id"]))
        assert resumed == run_id
        assert len(await engine.store.list()) == 1


async def test_driver_resume_reopens_work_whose_run_is_gone(tmp_path):
    async with stack(tmp_path) as (engine, work, _notes, _ledger, _locks):
        item = await work.create("Kayıp run", title="Kayıp")
        await work.update(item["id"], run_id="missing", status="active")
        hooks = EngineDriverHooks(engine)
        assert await hooks.resume_work(await work.get(item["id"])) is None
        reopened = await work.get(item["id"])
        assert reopened["status"] == "open" and reopened["run_id"] == ""


async def test_daemon_tick_starts_due_work_and_beats(tmp_path):
    async with stack(tmp_path) as (engine, work, notifications, ledger, _locks):
        beats = []

        class Hooks(EngineDriverHooks):
            async def beat(self, source_id, *, kind=None, detail=None):
                beats.append((source_id, kind))

        hooks = Hooks(engine, default_mode="chat", executor_model="worker")
        daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite",
                              ledger=ledger, tick_seconds=1,
                              reminder_seconds=(1,), block_after_seconds=3600)
        await daemon.open()
        try:
            await work.create("Vakti gelen iş", title="Vakit")
            await work.update((await work.list())[0]["id"], status="open")
            stats = await daemon.tick()
            assert stats["started"] == 1
            assert beats and beats[0][0] == "daemon"
            runs = await engine.store.list()
            assert len(runs) == 1
            run = await settle(engine, runs[0]["id"])
            assert run["status"] == "completed"
            # Second tick must not start a second run.
            await daemon.tick()
            assert len(await engine.store.list()) == 1
        finally:
            await daemon.close()


async def test_daemon_skips_waiting_and_blocked(tmp_path):
    async with stack(tmp_path) as (engine, work, notifications, ledger, _locks):
        hooks = EngineDriverHooks(engine, default_mode="chat", executor_model="worker")
        daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite",
                              ledger=ledger, tick_seconds=1,
                              reminder_seconds=(1,), block_after_seconds=3600)
        await daemon.open()
        try:
            waiting = await work.create("Karar bekleyen iş")
            await work.update(waiting["id"], status="waiting")
            await work.add_decision(waiting["id"], "Hangi biçim?")
            await work.update(waiting["id"], status="waiting")
            blocker = await work.create("Bağımlı iş", dependencies=["some-other"])
            await work.update(blocker["id"], status="open")
            stats = await daemon.tick()
            assert stats["started"] == 0
            assert stats["skipped"] >= 1
            # Waiting item never opens a run.
            assert await engine.store.list() == []
        finally:
            await daemon.close()


async def test_daemon_reminder_is_sent_once_per_interval(tmp_path):
    async with stack(tmp_path) as (engine, work, notifications, ledger, _locks):
        hooks = EngineDriverHooks(engine, default_mode="chat", executor_model="worker")
        daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite",
                              ledger=ledger, tick_seconds=1,
                              reminder_seconds=(1,), block_after_seconds=600)
        await daemon.open()
        try:
            item = await work.create("Karar işi")
            decision = await work.add_decision(item["id"], "Devam edeyim mi?")
            assert decision["question"]
            later = datetime.now(timezone.utc) + timedelta(seconds=5)
            await daemon.tick(now=later)
            notes = await notifications.list()
            reminders = [note for note in notes if "Karar" in note["title"]]
            assert len(reminders) == 1
            await daemon.tick(now=later)
            notes = await notifications.list()
            assert len([note for note in notes if "Karar" in note["title"]]) == 1
        finally:
            await daemon.close()


async def test_bridge_gate_token_survives_new_gate_instance(tmp_path):
    ledger = OperationLedger(tmp_path / "ops.sqlite")
    await ledger.open()
    try:
        from manager.bridges import BridgeRegistry
        registry = BridgeRegistry()
        registry.register({
            "id": "demo.local", "levels": ["read", "apply"],
            "idempotency": {"type": "keyed"},
            "evidence": {"format": "demo-evidence", "fields": ["summary"]},
            "errors": {"retryable": ["timeout"], "needs-user": ["missing-input"],
                       "external-down": ["connection-refused"]},
            "approval": {"apply": "per_call_token"},
        })
        first = BridgeGate(registry, token_store=ledger)
        decision = await first.authorize("implementer", "demo.local", "apply",
                                         approval_token="tok-1",
                                         idempotency_key="apply-1", payload={"x": 1})
        assert decision["approval_used"] is True
        second = BridgeGate(registry, token_store=ledger)
        with pytest.raises(ApprovalRequired):
            await second.authorize("implementer", "demo.local", "apply",
                                   approval_token="tok-1",
                                   idempotency_key="apply-1", payload={"x": 1})
    finally:
        await ledger.close()


async def test_engine_bridge_apply_uses_operation_ledger(tmp_path):
    async with stack(tmp_path) as (engine, _work, _notes, ledger, _locks):
        from manager.bridges import BridgeRegistry
        registry = BridgeRegistry()
        registry.register({
            "id": "demo.local", "levels": ["read", "apply"],
            "idempotency": {"type": "keyed"},
            "evidence": {"format": "demo-evidence", "fields": ["summary"]},
            "errors": {"retryable": ["timeout"], "needs-user": ["missing-input"],
                       "external-down": ["connection-refused"]},
            "approval": {"apply": "per_call_token"},
        })
        engine.bridge_gate.registry = registry
        decision = await engine.authorize_bridge(
            "implementer", "demo.local", "apply", approval_token="tok-2",
            idempotency_key="apply-2", payload={"x": 2})
        assert decision["operation_status"] == "pending"
        await engine.finish_bridge(decision, result={"ok": True})
        operations = await ledger.list()
        assert operations[0]["status"] == "applied"
        replay = await engine.authorize_bridge(
            "implementer", "demo.local", "apply", approval_token="tok-3",
            idempotency_key="apply-2", payload={"x": 2})
        assert replay["replayed"] is True


async def test_resource_lock_makes_second_run_wait(tmp_path):
    async with stack(tmp_path) as (engine, _work, _notes, _ledger, locks):
        engine.resource_lock_timeout = 0.2
        engine.resource_retry_seconds = 0.2
        key = f"workspace:{os.path.normcase(str(tmp_path.resolve()))}"
        lease = await locks.acquire(key, "other-run", timeout=0, ttl=60)
        try:
            run = await engine.create(__import__("manager.schema", fromlist=["RunRequest"]).RunRequest(
                prompt="Kilit testi", mode="chat", executor_model="worker", planner_model="auto",
                workspace=str(tmp_path)))
            await asyncio.sleep(0.4)
            saved = await engine.store.get(run["id"])
            assert saved["status"] == "queued"
            assert "kilidi" in (saved.get("error") or "").lower()
        finally:
            await lease.release()
        for _ in range(80):
            saved = await engine.store.get(run["id"])
            if saved["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.1)
        done = await engine.store.get(run["id"])
        assert done["status"] == "completed", done.get("error")
