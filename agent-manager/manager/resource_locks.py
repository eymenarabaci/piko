"""SQLite leases with shared readers and exclusive writers.

Faz 3'ün bağımsız kilit modülü. Kuyruk/slot/paralellik mühendisliği burada
değildir; bu dosya ortak okuyucuları ve tekil yazıcıyı zaman aşımlı,
TTL'li kiralamalarla koordine eder.

NON-REENTRANT DOKTRİN (work_record deadlock dersi):
    asyncio.Lock yeniden girilemez (non-reentrant). Bu modülde `self._lock`
    YALNIZCA SQL yazarken tutulur; kilidin içinde dış/await'li bir çağrı
    (uyku, başka bir public metot, ağ, dosya, run) ASLA yapılmaz. Bekleme
    döngüsü kilidin dışında `await asyncio.sleep(...)` ile döner, çünkü
    aynı task'in bu kilidi ikinci kez alması kilitli kalır ve süreç kilitlenir.
    Bu yüzden `acquire -> release -> temizle` zincirinde hiçbir metot,
    kilidi tutarken başka bir kilit alan metodu çağırmaz.
"""
from __future__ import annotations

import asyncio
import uuid
import os
import psutil
from datetime import datetime, timedelta, timezone

import aiosqlite


DEFAULT_TIMEOUT = 30.0
DEFAULT_TTL = 300.0
POLL_INTERVAL = 0.02


class LeaseLost(RuntimeError):
    """The lease expired or ownership changed; stop the protected work."""


class LockTimeout(TimeoutError):
    """Kaynak verilen süre içinde boşalmadı."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _clean(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Kaynak ve run kimliği boş olamaz.")
    return text


class _Lease:
    """`async with await locks.acquire(...)` ile kullanılan sahiplik tutamağı."""

    def __init__(self, locks: "ResourceLocks", resource: str, run_id: str, token: str):
        self._locks = locks
        self.resource = resource
        self.run_id = run_id
        self._released = False
        self.token = token

    async def __aenter__(self) -> "_Lease":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.release()
        return False

    async def renew(self, ttl: float) -> None:
        if self._released or not await self._locks.renew(self.resource, self.run_id, self.token, ttl):
            raise LeaseLost(f"Kaynak sahipliği kaybedildi: {self.resource}")

    async def assert_owned(self) -> None:
        if self._released or not await self._locks.owns(self.resource, self.run_id, self.token):
            raise LeaseLost(f"Kaynak sahipliği kaybedildi: {self.resource}")

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._locks.release(self.resource, self.run_id, token=self.token)


def _owner_alive(token):
    try:
        pid, created, _ = token.split(':', 2)
        return abs(psutil.Process(int(pid)).create_time() - float(created)) < .01
    except psutil.NoSuchProcess:
        return False
    except (ValueError, TypeError, psutil.AccessDenied):
        return True  # Unknown ownership is never permission to steal a live lease.


class ResourceLocks:
    """Ayrı `lock.db` dosyasında, WAL modunda, TTL'li kaynak kilidi.

    Her satır kaynak/run çiftinin bir sahibini tutar; shared okuyucular
    birlikte çalışabilir, exclusive yazıcı tüm diğer sahipleri dışlar; `expires_at` geçmişse satır
    ölü run'ın artığıdır ve yeni sahip tarafından devralınabilir. Aynı run'ın
    aynı kaynağı yeniden alması serbesttir (RLock benzeri sayaç, yalnız bu
    süreç içinde); sayaç sıfıra inince satır silinir.
    """

    def __init__(self, path):
        self.path = str(path)
        self.db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._holds: dict[tuple[str, str, str], int] = {}
        self._tokens: dict[tuple[str, str], str] = {}

    async def open(self) -> None:
        self.db = await aiosqlite.connect(self.path)
        db = self.db
        try:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA journal_mode=WAL")
            # Serialize migration with acquisition across independent processes.
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS resource_locks(
                    resource TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    token TEXT NOT NULL DEFAULT '',
                    shared INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(resource, run_id)
                )
            """)
            columns = await (await db.execute("PRAGMA table_info(resource_locks)")).fetchall()
            names = {row[1] for row in columns}
            primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            if primary != ["resource", "run_id"] or "shared" not in names:
                await db.execute("ALTER TABLE resource_locks RENAME TO resource_locks_legacy")
                await db.execute("""
                    CREATE TABLE resource_locks(
                        resource TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        acquired_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        token TEXT NOT NULL DEFAULT '',
                        shared INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(resource, run_id)
                    )
                """)
                token = "token" if "token" in names else "''"
                mode = "shared" if "shared" in names else "0"
                await db.execute(
                    "INSERT INTO resource_locks(resource,run_id,acquired_at,expires_at,token,shared) "
                    f"SELECT resource,run_id,acquired_at,expires_at,{token},{mode} FROM resource_locks_legacy")
                await db.execute("DROP TABLE resource_locks_legacy")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS resource_lock_expiry ON resource_locks(expires_at)")
            await db.commit()
        except BaseException:
            await db.rollback()
            await db.close()
            self.db = None
            raise

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    def _require_db(self):
        if self.db is None:
            raise RuntimeError("Kilit deposu açılmadı; önce open() çağır.")
        return self.db

    async def _try_acquire(self, resource: str, run_id: str, ttl: float,
                           shared: bool = False) -> str | None:
        db = self._require_db()
        now = _iso(_now())
        expiry = _iso(_now() + timedelta(seconds=ttl))
        async with self._lock:
            # The read/check/insert must be one write transaction: two SQLite
            # connections must never both observe an unheld workspace.
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute("DELETE FROM resource_locks WHERE resource=? AND expires_at<=?",
                                 (resource, now))
                async with db.execute(
                        "SELECT token,shared FROM resource_locks WHERE resource=? AND run_id=?",
                        (resource, run_id)) as cursor:
                    own = await cursor.fetchone()
                local = self._tokens.get((resource, run_id))
                if own and own[0] == local:
                    # Writer -> shared re-entry keeps the stronger writer lease.
                    # Reader -> writer requires releasing every reader handle;
                    # a silent upgrade would invalidate those handles' contract.
                    if own[1] and not shared:
                        await db.commit()
                        return None
                    await db.execute(
                        "UPDATE resource_locks SET expires_at=? WHERE resource=? AND run_id=? AND token=?",
                        (expiry, resource, run_id, local))
                    await db.commit()
                    key = (resource, run_id, local)
                    self._holds[key] = self._holds.get(key, 0) + 1
                    return local
                if own and _owner_alive(own[0]):
                    await db.commit()
                    return None
                # A restarted owner can recover only its own dead-process row.
                # Other live readers still prohibit an exclusive acquisition.
                async with db.execute(
                        "SELECT shared FROM resource_locks WHERE resource=? AND run_id<>?",
                        (resource, run_id)) as cursor:
                    others = await cursor.fetchall()
                if others and (not shared or any(not row[0] for row in others)):
                    await db.commit()
                    return None
                token = f'{os.getpid()}:{psutil.Process().create_time()}:{uuid.uuid4().hex}'
                await db.execute(
                    "INSERT INTO resource_locks(resource,run_id,acquired_at,expires_at,token,shared) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(resource,run_id) DO UPDATE SET "
                    "acquired_at=excluded.acquired_at,expires_at=excluded.expires_at,"
                    "token=excluded.token,shared=excluded.shared",
                    (resource, run_id, now, expiry, token, int(shared)))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            if local:
                self._holds.pop((resource, run_id, local), None)
            self._tokens[(resource, run_id)] = token
            self._holds[(resource, run_id, token)] = 1
            return token

    async def renew(self, resource, run_id, token, ttl) -> bool:
        db = self._require_db()
        now = _iso(_now())
        expiry = _iso(_now() + timedelta(seconds=max(0.001, float(ttl))))
        async with self._lock:
            cursor = await db.execute(
                "UPDATE resource_locks SET expires_at=? WHERE resource=? AND run_id=? AND token=? AND expires_at>?",
                (expiry, resource, run_id, token, now))
            await db.commit()
            return cursor.rowcount > 0

    async def owns(self, resource, run_id, token) -> bool:
        async with self._lock:
            cursor = await self._require_db().execute(
                "SELECT 1 FROM resource_locks WHERE resource=? AND run_id=? AND token=? AND expires_at>?",
                (resource, run_id, token, _iso(_now())))
            return await cursor.fetchone() is not None

    async def acquire(self, resource: str, run_id: str, *, timeout: float = DEFAULT_TIMEOUT,
                      ttl: float = DEFAULT_TTL, shared: bool = False) -> _Lease:
        """Kaynağı al; meşgulse `timeout` saniyeye kadar bekle, sonra LockTimeout.

        `shared=True` ortak okuyucu, varsayılan tekil yazıcı kiralamasıdır.
        `timeout=0` tek denemedir ve kaynak meşgulse anında LockTimeout atar.
        Bekleme döngüsü kilidin dışındadır (non-reentrant güvenliği).
        """
        if not isinstance(shared, bool):
            raise ValueError("shared must be a boolean")
        resource = _clean(resource)
        run_id = _clean(run_id)
        timeout = max(0.0, float(timeout))
        ttl = max(0.0, float(ttl))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            token = await self._try_acquire(resource, run_id, ttl, shared=shared)
            if token:
                return _Lease(self, resource, run_id, token)
            if timeout <= 0 or loop.time() >= deadline:
                raise LockTimeout(
                    f"Kaynak meşgul: {resource} (run={run_id}, timeout={timeout}s)")
            remaining = deadline - loop.time()
            await asyncio.sleep(min(POLL_INTERVAL, max(remaining, 0.0)))

    async def release(self, resource: str, run_id: str, *, token: str | None = None) -> bool:
        """Sahiplik sayacını düşür; sıfırlanınca satırı sil. Yalnız SQL + sözlük."""
        resource = _clean(resource)
        run_id = _clean(run_id)
        db = self._require_db()
        token = token or self._tokens.get((resource, run_id))
        if not token:
            return False
        key = (resource, run_id, token)
        async with self._lock:
            count = self._holds.get(key, 0)
            if count > 1:
                self._holds[key] = count - 1
                return False
            self._holds.pop(key, None)
            if self._tokens.get((resource, run_id)) == token:
                self._tokens.pop((resource, run_id), None)
            cursor = await db.execute(
                "DELETE FROM resource_locks WHERE resource=? AND run_id=? AND token=?",
                (resource, run_id, token))
            removed = cursor.rowcount > 0
            await cursor.close()
            await db.commit()
            return removed

    async def collect_stale(self, *, now: datetime | None = None) -> int:
        """Süresi geçmiş (TTL aşmış) satırları çöp topla; silinen satır sayısı."""
        db = self._require_db()
        cutoff = _iso(now or _now())
        async with self._lock:
            cursor = await db.execute(
                "DELETE FROM resource_locks WHERE expires_at<=?", (cutoff,))
            removed = cursor.rowcount
            await cursor.close()
            await self._prune_holds()
            await db.commit()
            return max(0, int(removed))

    async def _prune_holds(self) -> None:
        """Silinen satırların sözlük sayaçlarını temizle; kilidin içinde çağrılır."""
        if not self._holds:
            return
        async with self.db.execute("SELECT resource, run_id, token FROM resource_locks") as cursor:
            alive = set(await cursor.fetchall())
        for key in [k for k in self._holds if k not in alive]:
            self._holds.pop(key, None)

    async def temizle(self, run_id: str) -> int:
        """Bittiği kesin run'ın tüm kilitlerini bırak, sonra stale çöpleri topla.

        Dönüş: bu çağrıda serbest bırakılan + stale silinen satır sayısı.
        `_owned()` çağrısı kilit tutulmadan önce yapılır; kilidin içinde
        yeniden kilit alan hiçbir metot çağrılmaz (non-reentrant doktrini).
        """
        run_id = _clean(run_id)
        db = self._require_db()
        async with self._lock:
            removed = 0
            for (resource, owner), token in list(self._tokens.items()):
                if owner == run_id:
                    cursor = await db.execute("DELETE FROM resource_locks WHERE resource=? AND run_id=? AND token=?", (resource, run_id, token))
                    removed += cursor.rowcount
                    self._tokens.pop((resource, owner), None)
            cutoff = _iso(_now())
            cursor = await db.execute(
                "DELETE FROM resource_locks WHERE expires_at<=?", (cutoff,))
            removed += cursor.rowcount
            await cursor.close()
            await self._prune_holds()
            await db.commit()
            return max(0, int(removed))

    async def holder(self, resource: str) -> dict | None:
        """Return one holder (writer first); None means there is no stored lease."""
        db = self._require_db()
        resource = _clean(resource)
        async with self._lock:
            async with db.execute(
                    "SELECT resource, run_id, acquired_at, expires_at, token, shared "
                    "FROM resource_locks WHERE resource=? ORDER BY shared,acquired_at,run_id LIMIT 1", (resource,)) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return {"resource": row[0], "run_id": row[1],
                "acquired_at": row[2], "expires_at": row[3], "token": row[4], "shared": bool(row[5])}


# Wiring: Engine.drive() acquires one lease per workspace before a run starts
# and releases it (plus stale rows) in the run's finally block. Queueing and
# parallelism stay outside this module; it only enforces resource ownership.
