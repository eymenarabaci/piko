"""Async SQLite job/event index, separate from upstream graph checkpoints."""
from __future__ import annotations

import asyncio
import json
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
        """)
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
