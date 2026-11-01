"""De-duplicated notifications with preferences and quiet hours."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite


KINDS = {"delivery", "blocker", "decision", "delay", "info"}
SEVERITIES = {"low", "normal", "high"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_hhmm(value: str, fallback: str) -> time:
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        hour, minute = fallback.split(":", 1)
        return time(int(hour), int(minute))


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


DEFAULT_PREFERENCES = {
    "enabled": True,
    "quiet_start": "22:00",
    "quiet_end": "08:00",
    "timezone": "",
    "min_severity": "low",
}


class NotificationManager:
    def __init__(self, path):
        self.path = str(path)
        self.lock = asyncio.Lock()

    async def open(self):
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS notifications(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                work_id TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                dedupe_key TEXT NOT NULL DEFAULT '',
                suppressed_reason TEXT NOT NULL DEFAULT '',
                read_at TEXT,
                delivered_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS notification_dedupe
                ON notifications(dedupe_key) WHERE dedupe_key != '';
            CREATE INDEX IF NOT EXISTS notification_open
                ON notifications(delivered_at, read_at, created_at);
            CREATE TABLE IF NOT EXISTS notification_preferences(
                scope TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
        """)
        await self._write_defaults()
        await self.db.commit()

    async def close(self):
        await self.db.close()

    async def _write_defaults(self):
        await self.db.execute(
            "INSERT OR IGNORE INTO notification_preferences VALUES('default',?)",
            (json.dumps(DEFAULT_PREFERENCES, ensure_ascii=False),))

    async def get_preferences(self) -> dict:
        async with self.lock:
            async with self.db.execute(
                    "SELECT payload FROM notification_preferences WHERE scope='default'") as cur:
                row = await cur.fetchone()
        values = dict(DEFAULT_PREFERENCES)
        if row:
            try:
                values.update(json.loads(row[0]))
            except ValueError:
                pass
        return values

    async def set_preferences(self, **changes) -> dict:
        values = await self.get_preferences()
        if "enabled" in changes:
            values["enabled"] = bool(changes["enabled"])
        if "quiet_start" in changes:
            _parse_hhmm(changes["quiet_start"], "22:00")
            values["quiet_start"] = str(changes["quiet_start"])
        if "quiet_end" in changes:
            _parse_hhmm(changes["quiet_end"], "08:00")
            values["quiet_end"] = str(changes["quiet_end"])
        if "timezone" in changes:
            tz = str(changes["timezone"] or "")
            if tz:
                try:
                    ZoneInfo(tz)
                except ZoneInfoNotFoundError as exc:
                    raise ValueError("Bilinmeyen saat dilimi.") from exc
            values["timezone"] = tz
        if "min_severity" in changes:
            if changes["min_severity"] not in SEVERITIES:
                raise ValueError("Geçersiz önem düzeyi.")
            values["min_severity"] = changes["min_severity"]
        async with self.lock:
            await self.db.execute(
                "INSERT OR REPLACE INTO notification_preferences VALUES('default',?)",
                (json.dumps(values, ensure_ascii=False),))
            await self.db.commit()
        return values

    def _local_now(self, prefs: dict) -> datetime:
        tz_name = prefs.get("timezone") or ""
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
        return datetime.now().astimezone()

    def in_quiet_hours(self, prefs: dict, moment: datetime | None = None) -> bool:
        moment = moment or self._local_now(prefs)
        start = _minutes(_parse_hhmm(prefs.get("quiet_start", "22:00"), "22:00"))
        end = _minutes(_parse_hhmm(prefs.get("quiet_end", "08:00"), "08:00"))
        current = moment.hour * 60 + moment.minute
        if start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _record(row):
        keys = ("id", "kind", "severity", "title", "body", "work_id", "run_id",
                "dedupe_key", "suppressed_reason", "read_at", "delivered_at", "created_at")
        item = dict(zip(keys, row))
        item["read"] = bool(item["read_at"])
        item["deferred"] = not item["delivered_at"] and not item["suppressed_reason"]
        return item

    async def notify(self, kind: str, title: str, body: str = "", *, work_id: str = "",
                     run_id: str = "", dedupe_key: str = "", severity: str = "normal",
                     moment: datetime | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError("Geçersiz bildirim türü.")
        if severity not in SEVERITIES:
            raise ValueError("Geçersiz önem düzeyi.")
        title = str(title or "").strip()
        if not title:
            raise ValueError("Bildirim başlığı gerekli.")
        dedupe_key = str(dedupe_key or "").strip()
        prefs = await self.get_preferences()
        rank = {"low": 0, "normal": 1, "high": 2}
        reason = ""
        delivered_at = None
        if dedupe_key:
            async with self.lock:
                async with self.db.execute(
                        "SELECT * FROM notifications WHERE dedupe_key=?", (dedupe_key,)) as cur:
                    existing = await cur.fetchone()
            if existing:
                record = self._record(existing)
                record["duplicate"] = True
                return record
        if not prefs["enabled"]:
            reason = "disabled"
        elif rank[severity] < rank[prefs.get("min_severity", "low")]:
            reason = "below_severity"
        elif self.in_quiet_hours(prefs, moment):
            reason = ""
        else:
            delivered_at = _now()
        record = {
            "id": uuid.uuid4().hex, "kind": kind, "severity": severity,
            "title": title[:200], "body": str(body or "")[:4000],
            "work_id": str(work_id or ""), "run_id": str(run_id or ""),
            "dedupe_key": dedupe_key, "suppressed_reason": reason,
            "read_at": None, "delivered_at": delivered_at, "created_at": _now(),
        }
        async with self.lock:
            if dedupe_key:
                async with self.db.execute(
                        "SELECT * FROM notifications WHERE dedupe_key=?", (dedupe_key,)) as cur:
                    existing = await cur.fetchone()
                if existing:
                    result = self._record(existing)
                    result["duplicate"] = True
                    return result
            await self.db.execute(
                "INSERT INTO notifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (record["id"], record["kind"], record["severity"], record["title"],
                 record["body"], record["work_id"], record["run_id"], record["dedupe_key"],
                 record["suppressed_reason"], None, record["delivered_at"],
                 record["created_at"]))
            await self.db.commit()
        result = dict(record, read=False, deferred=not delivered_at and not reason,
                      duplicate=False)
        return result

    async def list(self, *, unread_only: bool = False, include_suppressed: bool = False,
                   limit: int = 100) -> list[dict]:
        clauses, params = [], []
        if unread_only:
            clauses.append("read_at IS NULL")
        if not include_suppressed:
            clauses.append("suppressed_reason=''")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        async with self.lock:
            async with self.db.execute(
                    f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT ?",
                    params) as cur:
                rows = await cur.fetchall()
        return [self._record(row) for row in rows]

    async def mark_read(self, notification_id: str) -> dict:
        async with self.lock:
            await self.db.execute(
                "UPDATE notifications SET read_at=? WHERE id=? AND read_at IS NULL",
                (_now(), notification_id))
            await self.db.commit()
            async with self.db.execute("SELECT * FROM notifications WHERE id=?",
                                       (notification_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise ValueError("Bildirim bulunamadı.")
        return self._record(row)

    async def mark_all_read(self) -> int:
        async with self.lock:
            cur = await self.db.execute(
                "UPDATE notifications SET read_at=? WHERE read_at IS NULL AND delivered_at IS NOT NULL",
                (_now(),))
            await self.db.commit()
            return cur.rowcount

    async def release_deferred(self, moment: datetime | None = None) -> list[dict]:
        """Deliver notifications deferred by quiet hours once the window has passed."""
        prefs = await self.get_preferences()
        if not prefs["enabled"] or self.in_quiet_hours(prefs, moment):
            return []
        async with self.lock:
            async with self.db.execute(
                    "SELECT * FROM notifications WHERE delivered_at IS NULL "
                    "AND suppressed_reason='' AND read_at IS NULL") as cur:
                rows = await cur.fetchall()
            records = [self._record(row) for row in rows]
            for record in records:
                await self.db.execute(
                    "UPDATE notifications SET delivered_at=? WHERE id=?",
                    (_now(), record["id"]))
            await self.db.commit()
        for record in records:
            record["delivered_at"] = _now()
            record["deferred"] = False
        return records
