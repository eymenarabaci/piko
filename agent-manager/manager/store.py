"""Async SQLite job/event index, separate from upstream graph checkpoints."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import aiosqlite


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()

    async def open(self):
        self.db = await aiosqlite.connect(str(self.path))
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS event_run ON events(run_id,id);
            CREATE TABLE IF NOT EXISTS run_messages(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                client_message_id TEXT,
                message TEXT NOT NULL,
                requested_delivery TEXT NOT NULL,
                delivery TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                target_step_id TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS run_message_client
                ON run_messages(run_id, client_message_id)
                WHERE client_message_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS run_message_pending
                ON run_messages(run_id, status, delivery, sequence);
        """)
        # A crash between terminal send and acknowledgement cannot be safely replayed.
        await self.db.execute("UPDATE run_messages SET status='delivery_unknown' WHERE status='sending'")
        await self.db.commit()

    async def close(self):
        await self.db.close()

    async def save(self, run):
        async with self.lock:
            await self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?)",
                                  (run["id"], json.dumps(run, ensure_ascii=False)))
            await self.db.commit()

    async def get(self, run_id):
        async with self.lock:
            async with self.db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)) as cur:
                row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def list(self):
        async with self.lock:
            async with self.db.execute("SELECT payload FROM runs ORDER BY rowid DESC LIMIT 200") as cur:
                rows = await cur.fetchall()
        return [json.loads(r[0]) for r in rows]

    async def active_count(self) -> int:
        """Count every active run, including records older than list()'s display cap."""
        async with self.lock:
            async with self.db.execute(
                "SELECT COUNT(*) FROM runs WHERE json_extract(payload, '$.status') "
                "IN ('queued', 'planning', 'running', 'waiting_quota')"
            ) as cur:
                row = await cur.fetchone()
        return int(row[0])

    async def conversation(self, run_id):
        run = await self.get(run_id)
        if run is None:
            return []
        conversation_id = run.get("conversation_id") or run["id"]
        async with self.lock:
            async with self.db.execute(
                "SELECT payload FROM runs WHERE id=? OR json_extract(payload,'$.conversation_id')=? ORDER BY rowid",
                (conversation_id, conversation_id),
            ) as cur:
                rows = await cur.fetchall()
        return [json.loads(row[0]) for row in rows]

    async def delete_many(self, run_ids):
        async with self.lock:
            try:
                for run_id in run_ids:
                    await self.db.execute("DELETE FROM run_messages WHERE run_id=?", (run_id,))
                    await self.db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                    await self.db.execute("DELETE FROM runs WHERE id=?", (run_id,))
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def event(self, run_id, kind, message, data=None):
        async with self.lock:
            await self.db.execute("INSERT INTO events(run_id,kind,message,created_at,data) VALUES(?,?,?,?,?)",
                                  (run_id, kind, message[:4000], now(), json.dumps(data or {}, ensure_ascii=False)))
            await self.db.commit()

    async def delete(self, run_id):
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM run_messages WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM runs WHERE id=?", (run_id,))
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def events(self, run_id, after=0):
        async with self.lock:
            async with self.db.execute("SELECT id,kind,message,created_at,data FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 150",
                                       (run_id, after)) as cur:
                rows = await cur.fetchall()
        events = [dict(id=r[0], kind=r[1], message=r[2], created_at=r[3], data=json.loads(r[4])) for r in rows]
        return {"events": events, "cursor": events[-1]["id"] if events else after}

    @staticmethod
    def _message_record(row):
        return dict(zip(("sequence", "id", "run_id", "client_message_id", "message",
                         "requested_delivery", "delivery", "status", "created_at",
                         "delivered_at", "target_step_id"), row))

    async def enqueue_message(self, run_id, message, delivery, client_message_id=""):
        """Persist user guidance in arrival order; client retries return one record."""
        if delivery not in {"now", "next_step", "next_plan"}:
            raise ValueError("Geçersiz mesaj teslim türü.")
        message = str(message).strip()
        if not message or len(message) > 8000:
            raise ValueError("Mesaj 1–8000 karakter arasında olmalı.")
        client_message_id = str(client_message_id or "").strip() or None
        if client_message_id is not None and len(client_message_id) > 100:
            raise ValueError("İstemci mesaj kimliği çok uzun.")
        actual_delivery = "next_step" if delivery == "now" else delivery
        async with self.lock:
            if client_message_id:
                cursor = await self.db.execute(
                    "SELECT * FROM run_messages WHERE run_id=? AND client_message_id=?",
                    (run_id, client_message_id))
                existing = await cursor.fetchone()
                if existing:
                    record = self._message_record(existing)
                    if record["message"] != message or record["requested_delivery"] != delivery:
                        raise ValueError("Mesaj kimliği farklı bir istek için kullanılmış.")
                    return record
            record_id = uuid.uuid4().hex
            await self.db.execute(
                "INSERT INTO run_messages(id,run_id,client_message_id,message,requested_delivery,"
                "delivery,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (record_id, run_id, client_message_id, message, delivery, actual_delivery,
                 "queued", now()))
            await self.db.commit()
            cursor = await self.db.execute("SELECT * FROM run_messages WHERE id=?", (record_id,))
            return self._message_record(await cursor.fetchone())

    async def list_messages(self, run_id):
        async with self.lock:
            cursor = await self.db.execute(
                "SELECT * FROM run_messages WHERE run_id=? ORDER BY sequence", (run_id,))
            return [self._message_record(row) for row in await cursor.fetchall()]

    async def claim_messages(self, run_id, deliveries, target_step_id=""):
        """Mark matching queued messages delivered in one ordered transaction."""
        deliveries = list(dict.fromkeys(deliveries))
        if not deliveries or any(item not in {"next_step", "next_plan"} for item in deliveries):
            raise ValueError("Geçersiz mesaj teslim türü.")
        placeholders = ",".join("?" for _ in deliveries)
        async with self.lock:
            cursor = await self.db.execute(
                f"SELECT * FROM run_messages WHERE run_id=? AND status='queued' "
                f"AND delivery IN ({placeholders}) ORDER BY sequence", (run_id, *deliveries))
            rows = await cursor.fetchall()
            if not rows:
                return []
            ids = [row[1] for row in rows]
            await self.db.executemany(
                "UPDATE run_messages SET status='delivered',delivered_at=?,target_step_id=? "
                "WHERE id=? AND status='queued'",
                [(now(), target_step_id or None, record_id) for record_id in ids])
            await self.db.commit()
            cursor = await self.db.execute(
                f"SELECT * FROM run_messages WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY sequence",
                ids)
            return [self._message_record(row) for row in await cursor.fetchall()]

    async def mark_live_delivery(self, message_id, target_step_id=""):
        """Acknowledge confirmed live terminal delivery; failed sends remain queued."""
        async with self.lock:
            cursor = await self.db.execute("SELECT * FROM run_messages WHERE id=?", (message_id,))
            existing = await cursor.fetchone()
            if existing is None:
                raise ValueError("Mesaj bulunamadı.")
            record = self._message_record(existing)
            if record["requested_delivery"] != "now":
                raise ValueError("Mesaj canlı teslim için istenmedi.")
            if record["status"] == "delivered" and record["delivery"] == "now":
                return record
            if record["status"] != "sending":
                raise ValueError("Mesaj canlı teslim için ayrılmadı.")
            await self.db.execute(
                "UPDATE run_messages SET delivery='now',status='delivered',"
                "delivered_at=?,target_step_id=? WHERE id=? AND status='sending'",
                (now(), target_step_id or None, message_id))
            await self.db.commit()
            cursor = await self.db.execute("SELECT * FROM run_messages WHERE id=?", (message_id,))
            return self._message_record(await cursor.fetchone())

    async def reserve_live_delivery(self, message_id):
        """Keep a live send out of the next-step consumer until it succeeds or fails."""
        async with self.lock:
            cursor = await self.db.execute("SELECT * FROM run_messages WHERE id=?", (message_id,))
            existing = await cursor.fetchone()
            if existing is None:
                raise ValueError("Mesaj bulunamadı.")
            record = self._message_record(existing)
            if record["requested_delivery"] != "now" or record["status"] != "queued":
                raise ValueError("Mesaj canlı teslim için ayrılamıyor.")
            await self.db.execute(
                "UPDATE run_messages SET status='sending' WHERE id=? AND status='queued'",
                (message_id,))
            await self.db.commit()
            cursor = await self.db.execute("SELECT * FROM run_messages WHERE id=?", (message_id,))
            return self._message_record(await cursor.fetchone())

    async def release_live_delivery(self, message_id):
        """Make a failed live send eligible for ordered next-step delivery."""
        async with self.lock:
            await self.db.execute(
                "UPDATE run_messages SET status='queued' WHERE id=? AND status='sending'",
                (message_id,))
            await self.db.commit()
            cursor = await self.db.execute("SELECT * FROM run_messages WHERE id=?", (message_id,))
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("Mesaj bulunamadı.")
            return self._message_record(row)
