"""Release regressions for durable adapter effects; no external services are called."""
import asyncio
import sqlite3

import pytest

from manager.operations import OperationLedger, OperationUncertain, effect_key


def test_effect_identity_is_scoped_and_content_order_independent():
    assert effect_key("a", "send", {"x": 1, "y": 2}) == effect_key("a", "send", {"y": 2, "x": 1})
    assert len({effect_key("a", "send", 1), effect_key("b", "send", 1),
                effect_key("a", "write", 1), effect_key("a", "send", 2)}) == 4


async def test_invalid_adapter_does_not_poison_the_effect_key(tmp_path):
    ledger = OperationLedger(tmp_path / "effects.sqlite")
    await ledger.open()
    try:
        with pytest.raises(TypeError):
            await ledger.run_effect(object(), work_id="w", purpose="write", content={})
        assert await ledger.list() == []
        result = await ledger.run_effect(lambda payload: "ok", work_id="w", purpose="write", content={})
        assert result["executed"] and result["result"] == "ok"
    finally:
        await ledger.close()


async def test_adapter_result_replays_after_reopen_without_second_call(tmp_path):
    path = tmp_path / "effects.sqlite"
    calls = []
    class Adapter:
        async def apply(self, payload):
            calls.append(payload)
            return {"receipt": "r1"}
    ledger = OperationLedger(path)
    await ledger.open()
    first = await ledger.run_effect(Adapter(), work_id="w", purpose="send", content={"id": 1}, payload={"body": "hello"})
    await ledger.close()
    ledger = OperationLedger(path)
    await ledger.open()
    try:
        replay = await ledger.run_effect(Adapter(), work_id="w", purpose="send", content={"id": 1})
        assert calls == [{"body": "hello"}]
        assert first["executed"] is True and replay["executed"] is False
        assert replay["result"] == {"receipt": "r1"}
        assert replay["operation"]["needs_verification"] is False
    finally:
        await ledger.close()


@pytest.mark.parametrize("error", [RuntimeError("adapter outcome uncertain"), asyncio.CancelledError()])
async def test_adapter_failure_requires_verification_and_is_not_retried(tmp_path, error):
    ledger = OperationLedger(tmp_path / "effects.sqlite")
    await ledger.open()
    calls = []
    async def adapter(payload):
        calls.append(payload)
        raise error
    try:
        with pytest.raises(type(error)):
            await ledger.run_effect(adapter, work_id="w", purpose="send", content={"id": 1})
        rows = await ledger.list()
        assert rows[0]["status"] == "unknown"
        assert rows[0]["verification_required"] is True
        assert rows[0]["needs_verification"] is True
        with pytest.raises(OperationUncertain):
            await ledger.run_effect(adapter, work_id="w", purpose="send", content={"id": 1})
        assert len(calls) == 1
    finally:
        await ledger.close()


async def test_concurrent_duplicate_does_not_invoke_second_adapter(tmp_path):
    ledger = OperationLedger(tmp_path / "effects.sqlite")
    await ledger.open()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def adapter(payload):
        calls.append(payload)
        entered.set()
        await release.wait()
        return "receipt"
    task = asyncio.create_task(ledger.run_effect(adapter, work_id="w", purpose="send", content={}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(OperationUncertain):
            await ledger.run_effect(adapter, work_id="w", purpose="send", content={})
        release.set()
        assert (await task)["result"] == "receipt"
        assert len(calls) == 1
    finally:
        release.set()
        await task
        await ledger.close()


async def test_old_schema_migrates_without_erasing_rows_or_budget(tmp_path):
    path = tmp_path / "effects.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        for status in ("pending", "unknown", "applied"):
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)", (status, "send", status, "hash", status, 1, "", "", "before", "before"))
    ledger = OperationLedger(path)
    await ledger.open()
    await ledger.configure_budget("w", token_limit=100)
    await ledger.consume("w", tokens=17)
    await ledger.close()
    ledger = OperationLedger(path)
    await ledger.open()
    try:
        rows = {row["id"]: row for row in await ledger.list()}
        assert set(rows) == {"pending", "unknown", "applied"}
        assert rows["pending"]["status"] == "unknown"
        assert all(rows[key]["verification_required"] for key in ("pending", "unknown"))
        assert rows["applied"]["status"] == "applied"
        assert rows["applied"]["needs_verification"] is False
        assert (await ledger.get_budget("w"))["token_used"] == 17
    finally:
        await ledger.close()
