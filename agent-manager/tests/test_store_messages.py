"""Exercise queue SQL without depending on a platform-specific async SQLite worker."""
import sqlite3

import pytest

from manager import store as store_module
from manager.store import Store


def _run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Unexpected asynchronous operation")


class AsyncQuery:
    def __init__(self, cursor):
        self.cursor = cursor

    def __await__(self):
        async def result():
            return self
        return result().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.cursor.close()

    async def fetchone(self):
        return self.cursor.fetchone()

    async def fetchall(self):
        return self.cursor.fetchall()


class AsyncDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")

    def execute(self, sql, params=()):
        return AsyncQuery(self.connection.execute(sql, params))

    async def executemany(self, sql, params):
        self.connection.executemany(sql, params)

    async def executescript(self, sql):
        self.connection.executescript(sql)

    async def commit(self):
        self.connection.commit()

    async def rollback(self):
        self.connection.rollback()

    async def close(self):
        self.connection.close()


@pytest.fixture
def store(monkeypatch):
    async def connect(_):
        return AsyncDatabase()
    monkeypatch.setattr(store_module.aiosqlite, "connect", connect)
    return Store(":memory:")


def test_instruction_queue_preserves_order_and_idempotency(store):
    async def scenario():
        await store.open()
        try:
            first = await store.enqueue_message("run-1", "İlk talimat", "next_step", "client-1")
            repeated = await store.enqueue_message("run-1", "İlk talimat", "next_step", "client-1")
            second = await store.enqueue_message("run-1", "Sonraki iş", "next_plan", "client-2")
            assert first == repeated
            assert first["sequence"] < second["sequence"]
            with pytest.raises(ValueError):
                await store.enqueue_message("run-1", "Farklı metin", "next_step", "client-1")
            assert [item["id"] for item in await store.list_messages("run-1")] == [first["id"], second["id"]]
            step = await store.claim_messages("run-1", ["next_step"], "implement")
            assert [item["id"] for item in step] == [first["id"]]
            assert step[0]["status"] == "delivered" and step[0]["target_step_id"] == "implement"
            assert await store.claim_messages("run-1", ["next_step"], "implement") == []
            plan = await store.claim_messages("run-1", ["next_plan"])
            assert [item["id"] for item in plan] == [second["id"]]
        finally:
            await store.close()
    _run_immediate(scenario())


def test_live_delivery_reservation_blocks_next_step_claim(store):
    async def scenario():
        await store.open()
        try:
            record = await store.enqueue_message("run-1", "Şimdi yönlendir", "now", "client-now")
            assert record["requested_delivery"] == "now" and record["delivery"] == "next_step"
            await store.reserve_live_delivery(record["id"])
            assert await store.claim_messages("run-1", ["next_step"]) == []
            delivered = await store.mark_live_delivery(record["id"], "active")
            assert delivered["delivery"] == "now" and delivered["target_step_id"] == "active"
            assert await store.claim_messages("run-1", ["next_step"]) == []
            fallback = await store.enqueue_message("run-1", "İkinci mesaj", "now", "client-fail")
            await store.reserve_live_delivery(fallback["id"])
            await store.release_live_delivery(fallback["id"])
            claimed = await store.claim_messages("run-1", ["next_step"], "next")
            assert [item["id"] for item in claimed] == [fallback["id"]]
        finally:
            await store.close()
    _run_immediate(scenario())
