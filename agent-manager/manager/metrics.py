"""Task outcome measurements kept separate from reviewer opinion and user acceptance."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import aiosqlite


def _now():
    return datetime.now(timezone.utc).isoformat()


class MetricsStore:
    def __init__(self, path):
        self.path = str(path)
        self.lock = asyncio.Lock()

    async def open(self):
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS task_metrics(
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL DEFAULT '',
                work_id TEXT NOT NULL DEFAULT '',
                task_kind TEXT NOT NULL DEFAULT 'general',
                model_id TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                success INTEGER,
                duration_seconds REAL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                repairs INTEGER NOT NULL DEFAULT 0,
                reviewer_verdict TEXT NOT NULL DEFAULT '',
                executed_test TEXT NOT NULL DEFAULT '',
                user_acceptance TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS metric_task ON task_metrics(task_kind, model_id);
            CREATE TABLE IF NOT EXISTS runtime_gauges(
                name TEXT PRIMARY KEY,
                value REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_counters(
                name TEXT PRIMARY KEY,
                value REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        await self.db.commit()

    async def close(self):
        await self.db.close()

    @staticmethod
    def _record(row):
        keys = ("id", "run_id", "work_id", "task_kind", "model_id", "role", "success",
                "duration_seconds", "input_tokens", "output_tokens", "cost", "repairs",
                "reviewer_verdict", "executed_test", "user_acceptance", "created_at")
        item = dict(zip(keys, row))
        item["success"] = None if item["success"] is None else bool(item["success"])
        return item

    async def record(self, *, run_id: str = "", work_id: str = "", task_kind: str = "general",
                     model_id: str = "", role: str = "", success: bool | None = None,
                     duration_seconds: float | None = None, input_tokens: int = 0,
                     output_tokens: int = 0, cost: float = 0, repairs: int = 0,
                     reviewer_verdict: str = "", executed_test: str = "",
                     user_acceptance: str = "") -> dict:
        row = (uuid.uuid4().hex, str(run_id), str(work_id), str(task_kind)[:60],
               str(model_id)[:100], str(role)[:40],
               None if success is None else int(bool(success)),
               None if duration_seconds is None else float(duration_seconds),
               max(0, int(input_tokens)), max(0, int(output_tokens)),
               max(0.0, float(cost)), max(0, int(repairs)),
               str(reviewer_verdict)[:40], str(executed_test)[:40],
               str(user_acceptance)[:40], _now())
        async with self.lock:
            await self.db.execute(
                "INSERT INTO task_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            await self.db.commit()
        return self._record(row)

    async def set_verdicts(self, metric_id: str, *, reviewer_verdict: str | None = None,
                           executed_test: str | None = None,
                           user_acceptance: str | None = None) -> dict:
        updates, params = [], []
        for key, value in (("reviewer_verdict", reviewer_verdict),
                           ("executed_test", executed_test),
                           ("user_acceptance", user_acceptance)):
            if value is not None:
                updates.append(f"{key}=?")
                params.append(str(value)[:40])
        if not updates:
            raise ValueError("Güncellenecek karar yok.")
        params.append(metric_id)
        async with self.lock:
            await self.db.execute(
                f"UPDATE task_metrics SET {', '.join(updates)} WHERE id=?", params)
            await self.db.commit()
            async with self.db.execute("SELECT * FROM task_metrics WHERE id=?",
                                       (metric_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise ValueError("Ölçüm kaydı bulunamadı.")
        return self._record(row)

    async def list(self, *, run_id: str = "", task_kind: str = "", model_id: str = "",
                   limit: int = 200) -> list[dict]:
        clauses, params = [], []
        for key, value in (("run_id", run_id), ("task_kind", task_kind), ("model_id", model_id)):
            if value:
                clauses.append(f"{key}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        async with self.lock:
            async with self.db.execute(
                    f"SELECT * FROM task_metrics {where} ORDER BY created_at DESC LIMIT ?",
                    params) as cur:
                rows = await cur.fetchall()
        return [self._record(row) for row in rows]

    async def summary(self, *, task_kind: str = "") -> dict:
        records = await self.list(task_kind=task_kind, limit=1000)
        groups: dict[str, dict] = {}
        for record in records:
            key = record["model_id"] or "unknown"
            group = groups.setdefault(key, {
                "model_id": key, "runs": 0, "successes": 0, "failures": 0,
                "durations": [], "cost": 0.0, "tokens": 0, "repairs": 0,
                "reviewer_pass": 0, "executed_test_pass": 0, "user_accepted": 0,
                "user_rejected": 0,
            })
            group["runs"] += 1
            if record["success"] is True:
                group["successes"] += 1
            elif record["success"] is False:
                group["failures"] += 1
            if record["duration_seconds"] is not None:
                group["durations"].append(record["duration_seconds"])
            group["cost"] += record["cost"]
            group["tokens"] += record["input_tokens"] + record["output_tokens"]
            group["repairs"] += record["repairs"]
            if record["reviewer_verdict"] == "pass":
                group["reviewer_pass"] += 1
            if record["executed_test"] == "pass":
                group["executed_test_pass"] += 1
            if record["user_acceptance"] == "accepted":
                group["user_accepted"] += 1
            elif record["user_acceptance"] == "rejected":
                group["user_rejected"] += 1
        result = []
        for group in groups.values():
            durations = group.pop("durations")
            total = group["successes"] + group["failures"]
            group["success_rate"] = round(group["successes"] / total, 4) if total else None
            group["avg_duration_seconds"] = (round(sum(durations) / len(durations), 2)
                                             if durations else None)
            group["cost_per_success"] = (round(group["cost"] / group["successes"], 4)
                                         if group["successes"] else None)
            result.append(group)
        result.sort(key=lambda item: (item["success_rate"] is None, -(item["success_rate"] or 0),
                                      -item["runs"]))
        return {"task_kind": task_kind or "all", "models": result,
                "record_count": len(records)}

    async def set_gauges(self, values: dict[str, float]) -> dict:
        """Upsert point-in-time runtime gauges (slot occupancy, waiters)."""
        if not values:
            return await self.runtime()
        moment = _now()
        async with self.lock:
            for name, value in values.items():
                await self.db.execute(
                    "INSERT INTO runtime_gauges(name,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value,"
                    "updated_at=excluded.updated_at",
                    (str(name)[:80], float(value), moment))
            await self.db.commit()
        return await self.runtime()

    async def add_counters(self, values: dict[str, float]) -> dict:
        """Increment monotonic runtime counters (queue wait count and seconds)."""
        if not values:
            return await self.runtime()
        moment = _now()
        async with self.lock:
            for name, value in values.items():
                await self.db.execute(
                    "INSERT INTO runtime_counters(name,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value,"
                    "updated_at=excluded.updated_at",
                    (str(name)[:80], float(value), moment))
            await self.db.commit()
        return await self.runtime()

    async def runtime(self) -> dict:
        async with self.lock:
            async with self.db.execute("SELECT name,value,updated_at FROM runtime_gauges") as cur:
                gauges = {row[0]: row[1] for row in await cur.fetchall()}
            async with self.db.execute("SELECT name,value FROM runtime_counters") as cur:
                counters = {row[0]: row[1] for row in await cur.fetchall()}
        wait_count = counters.get("slot_wait_count", 0.0)
        wait_total = counters.get("slot_wait_seconds_total", 0.0)
        return {
            "gauges": gauges,
            "counters": counters,
            "slot_occupancy_percent": (
                round(100.0 * gauges.get("slot_active", 0.0)
                      / gauges["max_concurrent_runs"], 1)
                if gauges.get("max_concurrent_runs") else 0.0),
            "slot_wait_avg_seconds": (round(wait_total / wait_count, 3)
                                     if wait_count else 0.0),
        }
