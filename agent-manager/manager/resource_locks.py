"""SQLite-backed resource locks: one run may own a resource at a time.

Faz 3'ün bağımsız kilit modülü. Kuyruk/slot/paralellik mühendisliği burada
değildir; bu dosya yalnız "aynı dosya/proje/cihaz üzerinde iki run koşamaz"
kuralını zaman aşımlı ve TTL'li biçimde uygular.

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
from datetime import datetime, timedelta, timezone

import aiosqlite


DEFAULT_TIMEOUT = 30.0
DEFAULT_TTL = 300.0
POLL_INTERVAL = 0.02


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

    def __init__(self, locks: "ResourceLocks", resource: str, run_id: str):
        self._locks = locks
        self.resource = resource
        self.run_id = run_id
        self._released = False

    async def __aenter__(self) -> "_Lease":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.release()
        return False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._locks.release(self.resource, self.run_id)


class ResourceLocks:
    """Ayrı `lock.db` dosyasında, WAL modunda, TTL'li kaynak kilidi.

    Her satır bir kaynağın tek sahibini tutar; `expires_at` geçmişse satır
    ölü run'ın artığıdır ve yeni sahip tarafından devralınabilir. Aynı run'ın
    aynı kaynağı yeniden alması serbesttir (RLock benzeri sayaç, yalnız bu
    süreç içinde); sayaç sıfıra inince satır silinir.
    """

    def __init__(self, path):
        self.path = str(path)
        self.db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._holds: dict[tuple[str, str], int] = {}

    async def open(self) -> None:
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS resource_locks(
                resource TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS resource_lock_expiry ON resource_locks(expires_at)")
        await self.db.commit()

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    def _require_db(self):
        if self.db is None:
            raise RuntimeError("Kilit deposu açılmadı; önce open() çağır.")
        return self.db

    async def _try_acquire(self, resource: str, run_id: str, ttl: float) -> bool:
        """Tek atomik deneme; yalnız SQL yazarken kilit tutar."""
        db = self._require_db()
        now = _now()
        acquired_at = _iso(now)
        expires_at = _iso(now + timedelta(seconds=ttl))
        async with self._lock:
            # 1) Aynı sahipse TTL'i yenile, süresi geçmişse devral: tek UPDATE.
            cursor = await db.execute(
                "UPDATE resource_locks SET run_id=?, acquired_at=?, expires_at=? "
                "WHERE resource=? AND (run_id=? OR expires_at<=?)",
                (run_id, acquired_at, expires_at, resource, run_id, acquired_at))
            taken = cursor.rowcount > 0
            await cursor.close()
            # 2) Satır yoksa yeni sahip olarak ekle; başkası aldıysa yok say.
            if not taken:
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO resource_locks"
                    "(resource, run_id, acquired_at, expires_at) VALUES(?,?,?,?)",
                    (resource, run_id, acquired_at, expires_at))
                taken = cursor.rowcount > 0
                await cursor.close()
            await db.commit()
            if taken:
                key = (resource, run_id)
                self._holds[key] = self._holds.get(key, 0) + 1
            return taken

    async def acquire(self, resource: str, run_id: str, *, timeout: float = DEFAULT_TIMEOUT,
                      ttl: float = DEFAULT_TTL) -> _Lease:
        """Kaynağı al; meşgulse `timeout` saniyeye kadar bekle, sonra LockTimeout.

        `timeout=0` tek denemedir ve kaynak meşgulse anında LockTimeout atar.
        Bekleme döngüsü kilidin dışındadır (non-reentrant güvenliği).
        """
        resource = _clean(resource)
        run_id = _clean(run_id)
        timeout = max(0.0, float(timeout))
        ttl = max(0.0, float(ttl))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if await self._try_acquire(resource, run_id, ttl):
                return _Lease(self, resource, run_id)
            if timeout <= 0 or loop.time() >= deadline:
                raise LockTimeout(
                    f"Kaynak meşgul: {resource} (run={run_id}, timeout={timeout}s)")
            remaining = deadline - loop.time()
            await asyncio.sleep(min(POLL_INTERVAL, max(remaining, 0.0)))

    async def release(self, resource: str, run_id: str) -> bool:
        """Sahiplik sayacını düşür; sıfırlanınca satırı sil. Yalnız SQL + sözlük."""
        resource = _clean(resource)
        run_id = _clean(run_id)
        db = self._require_db()
        key = (resource, run_id)
        async with self._lock:
            count = self._holds.get(key, 0)
            if count > 1:
                self._holds[key] = count - 1
                return False
            self._holds.pop(key, None)
            cursor = await db.execute(
                "DELETE FROM resource_locks WHERE resource=? AND run_id=?",
                (resource, run_id))
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
        async with self.db.execute("SELECT resource, run_id FROM resource_locks") as cursor:
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
            cursor = await db.execute(
                "DELETE FROM resource_locks WHERE run_id=?", (run_id,))
            removed = cursor.rowcount
            await cursor.close()
            cutoff = _iso(_now())
            cursor = await db.execute(
                "DELETE FROM resource_locks WHERE expires_at<=?", (cutoff,))
            removed += cursor.rowcount
            await cursor.close()
            await self._prune_holds()
            await db.commit()
            return max(0, int(removed))

    async def holder(self, resource: str) -> dict | None:
        """Tanı/denetim için kaynağın mevcut sahibini döndür (yoksa None)."""
        db = self._require_db()
        resource = _clean(resource)
        async with self._lock:
            async with db.execute(
                    "SELECT resource, run_id, acquired_at, expires_at "
                    "FROM resource_locks WHERE resource=?", (resource,)) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return {"resource": row[0], "run_id": row[1],
                "acquired_at": row[2], "expires_at": row[3]}


# Wiring: Engine.drive() acquires one lease per workspace before a run starts
# and releases it (plus stale rows) in the run's finally block. Queueing and
# parallelism stay outside this module; it only enforces resource ownership.
