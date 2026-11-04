"""Idempotent external operations, bounded budgets and role/tool permissions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import secrets
from datetime import datetime, timezone, timedelta

import aiosqlite


OPERATION_STATES = {"pending", "applied", "failed", "unknown"}
ROLE_TOOL_PERMISSIONS = {
    "implementer": {"read", "write", "draft", "apply"},
    "repairer": {"read", "write", "draft", "apply"},
    "reviewer": {"read"},
    "auditor": {"read"},
    "planner": {"read"},
    "scheduler": {"read", "draft"},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def effect_key(work_id: str, purpose: str, content) -> str:
    """Stable ledger key scoped to a work item, purpose and content."""
    material = json.dumps(
        {"work_id": str(work_id or ""), "purpose": str(purpose or ""), "content": content},
        ensure_ascii=False, sort_keys=True, default=str)
    return "effect:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class BudgetExceeded(ValueError):
    pass


class OperationConflict(ValueError):
    pass


class OperationUncertain(ValueError):
    pass


def allowed_tool(role: str, action: str) -> bool:
    return action in ROLE_TOOL_PERMISSIONS.get(role, set())


def require_tool(role: str, action: str) -> None:
    if not allowed_tool(role, action):
        raise PermissionError(f"{role} rolü '{action}' işlemine yetkili değil.")


class OperationLedger:
    """An external side effect gets one idempotency key. After a crash a pending
    operation becomes 'unknown' and is never silently executed a second time."""

    def __init__(self, path):
        self.path = str(path)
        self.lock = asyncio.Lock()

    async def open(self):
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1,
                result TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                verification_required INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS budgets(
                scope TEXT PRIMARY KEY,
                token_limit INTEGER NOT NULL DEFAULT 0,
                token_used INTEGER NOT NULL DEFAULT 0,
                seconds_limit INTEGER NOT NULL DEFAULT 0,
                seconds_used INTEGER NOT NULL DEFAULT 0,
                repairs_limit INTEGER NOT NULL DEFAULT 0,
                repairs_used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals(
                token_hash TEXT PRIMARY KEY,
                bridge TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT '',
                used_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS issued_approvals(
                token_hash TEXT PRIMARY KEY, bridge TEXT NOT NULL, level TEXT NOT NULL,
                payload_hash TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL, used_at TEXT, operation_key TEXT
            );
        """)
        async with self.db.execute("PRAGMA table_info(operations)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "verification_required" not in columns:
            await self.db.execute(
                "ALTER TABLE operations ADD COLUMN verification_required INTEGER NOT NULL DEFAULT 0")
        await self.db.execute(
            "UPDATE operations SET verification_required=1 WHERE status='unknown'")
        await self.db.execute(
            "UPDATE operations SET status='unknown',verification_required=1,updated_at=?,"
            "error=CASE WHEN error='' THEN 'Servis kesintisi; dış işlem sonucu doğrulanamadı.' ELSE error END "
            "WHERE status='pending'", (_now(),))
        await self.db.commit()

    async def close(self):
        await self.db.close()

    @staticmethod
    def _hash(payload) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         default=str).encode("utf-8")).hexdigest()

    @staticmethod
    def _record(row):
        keys = ("id", "kind", "idempotency_key", "payload_hash", "status", "attempts",
                "result", "error", "created_at", "updated_at", "verification_required")
        item = dict(zip(keys, row))
        try:
            item["result"] = json.loads(item["result"]) if item["result"] else None
        except ValueError:
            pass
        item["verification_required"] = bool(item.get("verification_required"))
        item["needs_verification"] = item["verification_required"] or item["status"] == "unknown"
        return item

    async def begin(self, kind: str, idempotency_key: str, payload=None) -> dict:
        kind = str(kind or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not kind or not idempotency_key:
            raise ValueError("İşlem türü ve idempotency anahtarı gerekli.")
        if len(idempotency_key) > 200:
            raise ValueError("Idempotency anahtarı çok uzun.")
        payload_hash = self._hash(payload if payload is not None else {})
        async with self.lock:
            async with self.db.execute(
                    "SELECT * FROM operations WHERE idempotency_key=?",
                    (idempotency_key,)) as cur:
                row = await cur.fetchone()
            if row:
                record = self._record(row)
                if record["payload_hash"] != payload_hash or record["kind"] != kind:
                    raise OperationConflict("Aynı anahtar farklı bir işlem için kullanılmış.")
                record["should_execute"] = False
                if record["status"] == "applied":
                    record["replayed"] = True
                elif record["status"] == "pending":
                    raise OperationUncertain("İşlem hâlâ sürüyor; ikinci kez başlatılmadı.")
                elif record["status"] == "unknown":
                    raise OperationUncertain(
                        "Önceki işlem sonucu belirsiz; dış sistem kontrol edilmeden tekrar çalıştırılmaz.")
                return record
            operation_id = uuid.uuid4().hex
            await self.db.execute(
                "INSERT INTO operations(id,kind,idempotency_key,payload_hash,status,"
                "attempts,result,error,created_at,updated_at,verification_required) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                (operation_id, kind, idempotency_key, payload_hash, "pending", 1, "", "",
                 _now(), _now()))
            await self.db.commit()
            async with self.db.execute("SELECT * FROM operations WHERE id=?",
                                       (operation_id,)) as cur:
                record = self._record(await cur.fetchone())
        record["should_execute"] = True
        record["replayed"] = False
        return record

    async def begin_effect(self, work_id: str, purpose: str, content,
                           *, kind: str = "") -> dict:
        """Reserve a stable key; uncertain prior effects require external verification."""
        key = effect_key(work_id, purpose, content)
        record = await self.begin(kind or purpose, key,
                                  {"work_id": work_id, "purpose": purpose, "content": content})
        record["effect_key"] = key
        return record

    async def run_effect(self, adapter, *, work_id: str, purpose: str, content,
                         payload=None) -> dict:
        """Run an adapter or replay its recorded result without silently retrying uncertainty.

        External effects and ledger commits are not atomic. An interrupted call
        must be checked against the external system before it may be resolved.
        """
        call = adapter if callable(adapter) else getattr(adapter, "apply", None)
        if not callable(call):
            call = getattr(adapter, "run", None)
        if not callable(call):
            raise TypeError("Etki adaptörü çağrılabilir veya apply/run metotlu olmalı.")
        record = await self.begin_effect(work_id, purpose, content)
        if not record["should_execute"]:
            return {"effect_key": record["effect_key"], "executed": False,
                    "result": record.get("result"), "operation": record}
        try:
            result = call(content if payload is None else payload)
            if hasattr(result, "__await__"):
                result = await result
        except BaseException as exc:
            await self.fail(record["id"], str(exc), uncertain=True)
            raise
        completed = await self.complete(record["id"], result)
        return {"effect_key": record["effect_key"], "executed": True,
                "result": result, "operation": completed}

    async def complete(self, operation_id: str, result=None) -> dict:
        async with self.lock:
            await self.db.execute(
                "UPDATE operations SET status='applied',result=?,error='',verification_required=0,updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False, default=str) if result is not None else "",
                 _now(), operation_id))
            await self.db.commit()
            async with self.db.execute("SELECT * FROM operations WHERE id=?",
                                       (operation_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise ValueError("İşlem bulunamadı.")
        return self._record(row)

    async def fail(self, operation_id: str, error: str, *, uncertain: bool = False) -> dict:
        async with self.lock:
            await self.db.execute(
                "UPDATE operations SET status=?,error=?,verification_required=?,updated_at=? WHERE id=?",
                ("unknown" if uncertain else "failed", str(error)[:2000],
                 1 if uncertain else 0, _now(), operation_id))
            await self.db.commit()
            async with self.db.execute("SELECT * FROM operations WHERE id=?",
                                       (operation_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise ValueError("İşlem bulunamadı.")
        return self._record(row)

    async def get(self, operation_id: str) -> dict | None:
        async with self.lock:
            async with self.db.execute("SELECT * FROM operations WHERE id=?",
                                       (operation_id,)) as cur:
                row = await cur.fetchone()
        return self._record(row) if row else None

    async def list(self, limit: int = 100) -> list[dict]:
        async with self.lock:
            async with self.db.execute(
                    "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(int(limit), 500)),)) as cur:
                rows = await cur.fetchall()
        return [self._record(row) for row in rows]

    async def issue_approval(self, *, bridge: str, level: str, payload=None,
                             scope: str = "", ttl_seconds: float = 600) -> str:
        if not bridge or level not in {"apply", "dangerous"} or not 0 < ttl_seconds <= 3600:
            raise ValueError("Geçersiz onay kapsamı veya süresi.")
        token = f"{bridge}-{secrets.token_urlsafe(32)}"
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        async with self.lock:
            await self.db.execute("INSERT INTO issued_approvals(token_hash,bridge,level,payload_hash,scope,expires_at) VALUES(?,?,?,?,?,?)",
                                  (digest, bridge, level, self._hash(payload if payload is not None else {}), scope, expires))
            await self.db.commit()
        return token

    async def consume_approval(self, token: str, *, bridge: str = "", level: str = "",
                               scope: str = "", payload=None, operation_key: str = "") -> bool:
        """Consume an issued, scoped token; only a completed identical operation may replay."""
        token = str(token or "").strip()
        if not token:
            raise ValueError("Onay token'ı gerekli.")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with self.lock:
            cursor = await self.db.execute("SELECT bridge,level,payload_hash,scope,expires_at,used_at,operation_key FROM issued_approvals WHERE token_hash=?", (digest,))
            row = await cursor.fetchone()
            expected_hash = self._hash(payload if payload is not None else {})
            if row is None or row[:4] != (bridge, level, expected_hash, scope):
                return False
            if row[5]:
                if not operation_key or row[6] != operation_key:
                    return False
                cursor = await self.db.execute("SELECT status,payload_hash,kind FROM operations WHERE idempotency_key=?", (operation_key,))
                operation = await cursor.fetchone()
                return operation == ("applied", expected_hash, bridge)
            if datetime.fromisoformat(row[4]) <= datetime.now(timezone.utc):
                return False
            cursor = await self.db.execute("UPDATE issued_approvals SET used_at=?,operation_key=? WHERE token_hash=? AND used_at IS NULL", (_now(), operation_key, digest))
            await self.db.execute("INSERT OR IGNORE INTO approvals(token_hash,bridge,level,scope,used_at) VALUES(?,?,?,?,?)", (digest, bridge, level, scope, _now()))
            await self.db.commit()
            return cursor.rowcount > 0

    async def approval_used(self, token: str) -> bool:
        digest = hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()
        async with self.lock:
            async with self.db.execute(
                    "SELECT 1 FROM approvals WHERE token_hash=?", (digest,)) as cur:
                return await cur.fetchone() is not None

    async def configure_budget(self, scope: str, *, token_limit: int = 0,
                               seconds_limit: int = 0, repairs_limit: int = 0) -> dict:
        scope = str(scope or "").strip()
        if not scope:
            raise ValueError("Bütçe kapsamı gerekli.")
        for value in (token_limit, seconds_limit, repairs_limit):
            if int(value) < 0:
                raise ValueError("Bütçe sınırı negatif olamaz.")
        async with self.lock:
            await self.db.execute(
                "INSERT INTO budgets(scope,token_limit,token_used,seconds_limit,seconds_used,"
                "repairs_limit,repairs_used,updated_at) VALUES(?,?,0,?,0,?,0,?) "
                "ON CONFLICT(scope) DO UPDATE SET token_limit=excluded.token_limit,"
                "seconds_limit=excluded.seconds_limit,repairs_limit=excluded.repairs_limit,"
                "updated_at=excluded.updated_at",
                (scope, int(token_limit), int(seconds_limit), int(repairs_limit), _now()))
            await self.db.commit()
        return await self.get_budget(scope)

    async def get_budget(self, scope: str) -> dict:
        async with self.lock:
            async with self.db.execute("SELECT * FROM budgets WHERE scope=?", (scope,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise ValueError("Bütçe bulunamadı.")
        keys = ("scope", "token_limit", "token_used", "seconds_limit", "seconds_used",
                "repairs_limit", "repairs_used", "updated_at")
        return dict(zip(keys, row))

    async def consume(self, scope: str, *, tokens: int = 0, seconds: int = 0,
                      repairs: int = 0) -> dict:
        for value in (tokens, seconds, repairs):
            if int(value) < 0:
                raise ValueError("Bütçe tüketimi negatif olamaz.")
        current = await self.get_budget(scope)
        tests = (
            ("token", current["token_used"] + int(tokens), current["token_limit"]),
            ("seconds", current["seconds_used"] + int(seconds), current["seconds_limit"]),
            ("repairs", current["repairs_used"] + int(repairs), current["repairs_limit"]),
        )
        for label, used, limit in tests:
            if limit and used > limit:
                raise BudgetExceeded(f"{scope} {label} bütçesi aşıldı ({used}/{limit}).")
        async with self.lock:
            await self.db.execute(
                "UPDATE budgets SET token_used=token_used+?,seconds_used=seconds_used+?,"
                "repairs_used=repairs_used+?,updated_at=? WHERE scope=?",
                (int(tokens), int(seconds), int(repairs), _now(), scope))
            await self.db.commit()
        return await self.get_budget(scope)
