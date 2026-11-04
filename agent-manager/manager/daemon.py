"""Unattended driver core: a deterministic 10-second tick over durable work items.

The daemon owns *when* work is picked up, resumed or reminded. It never talks to
the engine directly: everything that creates or resumes a run goes through the
:class:`DriverHooks` protocol. The main application supplies the adapter later.

State machine handled here::

    open -> active -> waiting -> active -> done
    blocked -> open   (only when its declared dependencies are all done)

``waiting`` and ``blocked`` items never occupy the loop; the next ready item is
started while a decision is outstanding.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiosqlite

from .health import DAEMON_KIND, DAEMON_SOURCE_ID
from .recovery import collect_proposals, proposal_for, summarize as summarize_recovery
from .single_instance import InstanceLock

log = logging.getLogger("manager.daemon")

TICK_SECONDS = 10
REMINDER_SECONDS = (1, 4, 24)
BLOCK_AFTER_SECONDS = 72
WORK_LIMIT = 500
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_aware(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _epoch() -> datetime:
    return datetime.max.replace(tzinfo=timezone.utc)


@runtime_checkable
class DriverHooks(Protocol):
    """Adapter the main application implements around ``Engine``.

    Both calls must be idempotent for the given work id: the daemon retries after
    a crash and may see the same ``active`` record again. Returning ``None``
    means "nothing to attach yet" and is retried on the next tick.
    """

    async def resume_work(self, work: dict) -> str | None:
        """Re-attach an ``active`` work item to its existing run."""

    async def start_work(self, work: dict) -> str | None:
        """Create one run for a due ``open`` item and return its run id."""

    async def notify(self, kind: str, title: str, body: str = "", *,
                     work_id: str = "", run_id: str = "", dedupe_key: str = "",
                     severity: str = "normal") -> None:
        """Surface a decision reminder or blocker to the user.

        The main application forwards this to NotificationManager, which
        de-duplicates by ``dedupe_key`` and honours quiet hours.
        """

    async def beat(self, source_id: str, *, kind: str | None = None,
                   detail: str | None = None) -> None:
        """Stamp the daemon heartbeat read by the external watchdog."""

    async def on_recovery(self, proposals) -> None:
        """Receive unknown-operation proposals; never apply them here."""


class DriverState:
    """Small daemon-owned bookkeeping table; work records stay untouched."""

    def __init__(self, path):
        self.path = str(path)
        self.lock = asyncio.Lock()

    async def open(self):
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS decision_reminders(
                work_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                last_index INTEGER NOT NULL DEFAULT -1,
                blocked_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(work_id, decision_id)
            );
            CREATE TABLE IF NOT EXISTS daemon_blocks(
                work_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                blocked_at TEXT NOT NULL
            );
        """)
        await self.db.commit()

    async def close(self):
        await self.db.close()

    async def get_reminder(self, work_id: str, decision_id: str) -> dict | None:
        async with self.lock:
            async with self.db.execute(
                    "SELECT last_index,blocked_at,updated_at FROM decision_reminders "
                    "WHERE work_id=? AND decision_id=?", (work_id, decision_id)) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {"last_index": int(row[0]), "blocked_at": row[1] or "",
                "updated_at": row[2]}

    async def set_reminder(self, work_id: str, decision_id: str, index: int) -> None:
        async with self.lock:
            await self.db.execute(
                "INSERT INTO decision_reminders(work_id,decision_id,last_index,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(work_id,decision_id) DO UPDATE SET "
                "last_index=excluded.last_index,updated_at=excluded.updated_at",
                (work_id, decision_id, int(index), _utcnow().isoformat()))
            await self.db.commit()

    async def mark_blocked(self, work_id: str, decision_id: str, moment: datetime) -> None:
        async with self.lock:
            await self.db.execute(
                "INSERT INTO decision_reminders(work_id,decision_id,last_index,blocked_at,"
                "updated_at) VALUES(?,?,-1,?,?) "
                "ON CONFLICT(work_id,decision_id) DO UPDATE SET "
                "blocked_at=excluded.blocked_at,updated_at=excluded.updated_at",
                (work_id, decision_id, moment.isoformat(), _utcnow().isoformat()))
            await self.db.commit()

    async def mark_daemon_block(self, work_id: str, reason: str, moment: datetime) -> None:
        async with self.lock:
            await self.db.execute(
                "INSERT INTO daemon_blocks(work_id,reason,blocked_at) VALUES(?,?,?) "
                "ON CONFLICT(work_id) DO UPDATE SET reason=excluded.reason,"
                "blocked_at=excluded.blocked_at",
                (work_id, str(reason)[:600], moment.isoformat()))
            await self.db.commit()

    async def is_daemon_blocked(self, work_id: str) -> bool:
        async with self.lock:
            async with self.db.execute(
                    "SELECT 1 FROM daemon_blocks WHERE work_id=?", (work_id,)) as cur:
                return await cur.fetchone() is not None


class DriverDaemon:
    def __init__(self, work, hooks: DriverHooks, *, state_path, ledger=None,
                 tick_seconds: int = TICK_SECONDS,
                 reminder_seconds=REMINDER_SECONDS,
                 block_after_seconds: int = BLOCK_AFTER_SECONDS,
                 clock=_utcnow, lock_path=None):
        self.work = work
        self.hooks = hooks
        self.state = DriverState(state_path)
        self.lock_path = Path(lock_path) if lock_path else Path(state_path).with_name("daemon.lock")
        self.ledger = ledger
        self.tick_seconds = max(1, int(tick_seconds))
        self.reminder_seconds = tuple(max(1, int(value)) for value in reminder_seconds)
        self.block_after_seconds = max(1, int(block_after_seconds))
        self.clock = clock
        self.recovery_proposals: list[dict] = []
        self._stop = asyncio.Event()
        self._opened = False
        self._resumed: set[str] = set()
        self._started: set[str] = set()
        self._dependency_flagged: set[str] = set()
        self.stats = {"tick": 0, "resumed": 0, "started": 0, "reminders": 0,
                      "blocked": 0, "skipped": 0, "errors": 0, "repairs": 0,
                      "dependency_errors": 0}

    async def open(self):
        if self._opened:
            return
        await self.state.open()
        self._opened = True
        if self.ledger is not None:
            self.recovery_proposals = await collect_proposals(self.ledger)
            summary = summarize_recovery(self.recovery_proposals)
            log.info("Kurtarma taraması: %s belirsiz işlem, %s soru; otomatik düzeltme yok.",
                     summary["unknown_count"], summary["question_count"])
            for proposal in self.recovery_proposals:
                log.info("Kurtarma sorusu: %s", proposal["question"])
            callback = getattr(self.hooks, "on_recovery", None)
            if callback is not None:
                # Surfaced as questions only; nothing is applied without an
                # explicit user answer through the main application.
                await callback(self.recovery_proposals)

    async def close(self):
        if not self._opened:
            return
        await self.state.close()
        self._opened = False

    def stop(self):
        self._stop.set()

    def stop_daemon(self):
        """Alias kept for callers that already speak the trigger-daemon API."""
        self.stop()

    async def run_daemon(self):
        """Alias kept for callers that already speak the trigger-daemon API."""
        await self.run()

    async def run(self):
        lock = InstanceLock(self.lock_path)
        if not lock.acquire():
            log.warning("Başka bir sürücü zaten çalışıyor (%s); bu süreç çalışmadan çıkıyor.",
                        self.lock_path)
            self.stats["duplicate"] = self.stats.get("duplicate", 0) + 1
            return
        try:
            await self.open()
            log.info("Sürücü kilidi alındı: %s", self.lock_path)
            while not self._stop.is_set():
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.stats["errors"] += 1
                    log.exception("Sürücü tick hatası; döngü devam ediyor.")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.close()
            lock.release()

    async def tick(self, now: datetime | None = None) -> dict:
        if not self._opened:
            await self.open()
        moment = now or self.clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("Sürücü zamanı saat dilimi içermeli.")
        moment = moment.astimezone(timezone.utc)
        self.stats["tick"] += 1
        await self._beat("tick")

        # Dependencies are global: completed work outside the display window
        # must still unblock current work.
        index = await self.work.dependency_index()
        done_ids = {work_id for work_id, entry in index.items()
                    if entry["status"] == "done"}
        works = await self.work.list(limit=WORK_LIMIT)
        works = [await self._resolve_blocked(item, done_ids) for item in works]

        for item in works:
            if item["status"] == "active":
                await self._resume(item)

        for item, reason in self._dependency_errors(works, index, done_ids):
            await self._flag_dependency_error(item, reason)

        ready = [item for item in works if item["status"] == "open"]
        ready.sort(key=lambda item: (
            _parse_aware(item.get("due_at")) or _epoch(),
            PRIORITY_RANK.get(item.get("priority", "normal"), 2),
            str(item.get("created_at") or ""),
            str(item.get("id") or ""),
        ))
        for item in ready:
            if not self._dependencies_ready(item, done_ids):
                self.stats["skipped"] += 1
                continue
            if not self._is_due(item, moment):
                continue
            await self._start(item)

        for item in works:
            if item["status"] == "waiting":
                await self._process_decisions(item, moment)
        return dict(self.stats)

    async def _beat(self, detail: str = "") -> None:
        """Stamp the daemon heartbeat for the external watchdog."""
        callback = getattr(self.hooks, "beat", None)
        if callback is None:
            return
        try:
            await callback(DAEMON_SOURCE_ID, kind=DAEMON_KIND, detail=detail or None)
        except Exception:
            self.stats["errors"] += 1
            log.debug("Sürücü kalp atışı yazılamadı.", exc_info=True)

    async def _resolve_blocked(self, item: dict, done_ids: set[str]) -> dict:
        if item["status"] != "blocked":
            return item
        dependencies = [str(value) for value in item.get("dependencies") or []]
        if not dependencies or not set(dependencies) <= done_ids:
            return item
        if await self.state.is_daemon_blocked(item["id"]):
            return item
        try:
            updated = await self.work.update(
                item["id"], status="open",
                next_action="Bağımlılıklar tamamlandı; iş yeniden sıraya alındı.")
        except Exception:
            self.stats["errors"] += 1
            log.exception("Bloke iş açılamadı: %s", item.get("id"))
            return item
        log.info("Bloke iş bağımlılıkları çözüldü: %s", item["id"])
        return updated

    @staticmethod
    def _dependencies_ready(item: dict, done_ids: set[str]) -> bool:
        dependencies = [str(value) for value in item.get("dependencies") or []]
        return set(dependencies) <= done_ids

    @staticmethod
    def _dependency_errors(works: list[dict], index: dict, done_ids: set[str]):
        """Jobs whose dependencies can never complete (gap I-02).

        Missing, cancelled and cyclic dependencies are surfaced as explicit
        errors instead of being skipped silently forever. The peel treats every
        unresolved dependency as blocking; what remains after the fixpoint
        cannot resolve: its unmet dependency is unknown, is cancelled, or sits
        in the same stuck (cyclic) closure.
        """
        known = set(index)
        cancelled = {work_id for work_id, entry in index.items()
                     if entry["status"] == "cancelled"}
        pending = {
            work_id: {str(dep) for dep in entry.get("dependencies") or []
                      if str(dep) not in done_ids}
            for work_id, entry in index.items() if entry["status"] != "done"
        }
        remaining = set(pending)
        changed = True
        while changed:
            changed = False
            for work_id in list(remaining):
                deps = pending[work_id]
                if (deps & remaining) or (deps & cancelled) or (deps - known):
                    continue
                remaining.discard(work_id)
                changed = True
        reasons: dict[str, str] = {}
        for work_id in remaining:
            deps = pending[work_id]
            missing = sorted(deps - known)
            stopped = sorted(deps & cancelled)
            cyclic = sorted(deps & remaining)
            if missing:
                reasons[work_id] = f"eksik bağımlılık: {', '.join(missing[:3])}"
            elif stopped:
                reasons[work_id] = f"iptal edilmiş bağımlılık: {', '.join(stopped[:3])}"
            else:
                reasons[work_id] = f"çözülemeyen bağımlılık döngüsü: {', '.join(cyclic[:3])}"
        flagged = []
        for item in works:
            work_id = str(item.get("id") or "")
            if item.get("status") in {"open", "blocked"} and work_id in reasons:
                flagged.append((item, reasons[work_id]))
        return flagged

    async def _flag_dependency_error(self, item: dict, reason: str) -> None:
        """Surface a dependency defect once per job: log, notification, record."""
        work_id = str(item.get("id") or "")
        if not work_id or work_id in self._dependency_flagged:
            return
        self._dependency_flagged.add(work_id)
        self.stats["dependency_errors"] += 1
        log.error("İş bağımlılık hatası: %s (%s)", work_id, reason)
        try:
            await self.work.update(work_id, next_action=f"Bağımlılık hatası: {reason}")
        except Exception:
            self.stats["errors"] += 1
            log.exception("Bağımlılık hatası iş kaydına yazılamadı: %s", work_id)
        callback = getattr(self.hooks, "notify", None)
        if callback is None:
            return
        try:
            await callback("dependency_error",
                           f"Bağımlılık hatası: {item.get('title') or work_id}",
                           reason, work_id=work_id, dedupe_key=f"dep:{work_id}",
                           severity="high")
        except Exception:
            self.stats["errors"] += 1
            log.exception("Bağımlılık hatası bildirilemedi: %s", work_id)

    @staticmethod
    def _is_due(item: dict, moment: datetime) -> bool:
        due = _parse_aware(item.get("due_at"))
        return due is None or due <= moment

    async def _resume(self, item: dict) -> None:
        run_id = str(item.get("run_id") or "")
        if not run_id:
            log.warning("Aktif işin run kaydı yok; elle inceleme gerekli: %s", item["id"])
            return
        if run_id in self._resumed:
            return
        try:
            resumed = await self.hooks.resume_work(dict(item))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.stats["errors"] += 1
            log.exception("Aktif iş geri bağlanamadı: %s", item["id"])
            return
        if not resumed:
            log.info("Aktif iş henüz bağlanmadı; sonraki tick yeniden denenecek: %s",
                     item["id"])
            return
        self._resumed.add(run_id)
        self.stats["resumed"] += 1
        log.info("Aktif iş kaldığı yerden bağlandı: %s -> %s", item["id"], resumed)

    async def _start(self, item: dict) -> None:
        if item["id"] in self._started:
            return
        self._started.add(item["id"])
        try:
            current = await self.work.get(item["id"])
            if current is None or current.get("status") != "open" or current.get("run_id"):
                return
            await self._start_pending(current)
        finally:
            # This is an in-flight guard only. Persistent run linkage protects
            # retries; a later repair generation must be allowed to start.
            self._started.discard(item["id"])

    async def _start_pending(self, item: dict) -> None:
        repair = None
        try:
            repair = await self.work.pending_repair(item["id"])
        except Exception:
            self.stats["errors"] += 1
            log.exception("Onarım durumu okunamadı: %s", item["id"])
        start_repair = getattr(self.hooks, "start_repair", None)
        try:
            # The adapter links the created run to this work row; a retried
            # tick therefore finds ``run_id`` set and never creates a second run.
            if repair is not None and start_repair is not None:
                run_id = await start_repair(dict(item), dict(repair))
            else:
                run_id = await self.hooks.start_work(dict(item))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.stats["errors"] += 1
            log.exception("İş başlatılamadı: %s", item["id"])
            return
        if not run_id:
            log.info("İş için henüz run açılmadı; sonraki tick yeniden denenecek: %s",
                     item["id"])
            return
        self.stats["started"] += 1
        if repair is not None and start_repair is not None:
            self.stats["repairs"] += 1
            log.info("Onarım run'ı başlatıldı: %s (v%s) -> %s",
                     item["id"], repair.get("version"), run_id)
        try:
            linked = await self.work.get(item["id"])
            if not linked or linked.get("run_id") != str(run_id):
                await self.work.create_for_run(
                    {"id": str(run_id), "conversation_id": item.get("conversation_id", ""),
                     "prompt": item.get("goal", "")},
                    work_id=item["id"])
        except Exception:
            self.stats["errors"] += 1
            log.exception("Run açıldı ama iş kaydına bağlanamadı: %s -> %s",
                          item["id"], run_id)
            return
        log.info("Vakti gelen iş başlatıldı: %s -> %s", item["id"], run_id)

    async def _process_decisions(self, item: dict, moment: datetime) -> None:
        for decision in item.get("decisions") or []:
            if decision.get("status") != "waiting":
                continue
            decision_id = str(decision.get("id") or "")
            if not decision_id:
                continue
            created = _parse_aware(decision.get("created_at"))
            if created is None:
                continue
            state = await self.state.get_reminder(item["id"], decision_id)
            if state and state["blocked_at"]:
                continue
            elapsed = (moment - created).total_seconds()

            if elapsed >= self.block_after_seconds:
                await self._block_for_decision(item, decision, moment)
                return

            due_indexes = [index for index, delay in enumerate(self.reminder_seconds)
                           if elapsed >= delay]
            if not due_indexes:
                continue
            index = due_indexes[-1]
            last_index = state["last_index"] if state else -1
            if index <= last_index:
                continue
            await self.state.set_reminder(item["id"], decision_id, index)
            self.stats["reminders"] += 1
            question = str(decision.get("question") or "Karar bekleniyor.")
            await self._notify(
                "decision",
                f"Karar bekleniyor: {item.get('title') or item['id']}",
                (f"{question}\n"
                 f"Yanıtlanmadı; {index + 1}. hatırlatma "
                 f"({int(elapsed)} sn). İş bu arada diğer işleri bloke etmiyor."),
                work_id=item["id"], run_id=str(item.get("run_id") or ""),
                dedupe_key=f"decision-reminder:{item['id']}:{decision_id}:{index}",
                severity="high" if index == len(self.reminder_seconds) - 1 else "normal")
            log.info("Karar hatırlatması gönderildi: %s (%s. aralık)", item["id"], index + 1)

    async def _block_for_decision(self, item: dict, decision: dict,
                                  moment: datetime) -> None:
        decision_id = str(decision.get("id") or "")
        await self.state.mark_blocked(item["id"], decision_id, moment)
        await self.state.mark_daemon_block(
            item["id"], "Karar süresi doldu.", moment)
        self.stats["blocked"] += 1
        try:
            await self.work.update(
                item["id"], status="blocked",
                next_action=(f"Karar {self.block_after_seconds} saniye içinde verilmedi; "
                             "iş bloke edildi."))
        except Exception:
            self.stats["errors"] += 1
            log.exception("İş bloke edilemedi: %s", item["id"])
            return
        await self._notify(
            "blocker",
            f"İş bloke edildi: {item.get('title') or item['id']}",
            (f"{decision.get('question') or 'Karar bekleniyor.'}\n"
             f"{self.block_after_seconds} saniye yanıt gelmedi; iş 'blocked' durumuna alındı."),
            work_id=item["id"], run_id=str(item.get("run_id") or ""),
            dedupe_key=f"decision-blocked:{item['id']}:{decision_id}",
            severity="high")
        log.warning("Karar süresi doldu, iş bloke edildi: %s", item["id"])

    async def _notify(self, kind: str, title: str, body: str, *, work_id: str = "",
                      run_id: str = "", dedupe_key: str = "",
                      severity: str = "normal") -> None:
        callback = getattr(self.hooks, "notify", None)
        if callback is None:
            return
        try:
            await callback(kind, title, body, work_id=work_id, run_id=run_id,
                           dedupe_key=dedupe_key, severity=severity)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.stats["errors"] += 1
            log.exception("Bildirim gönderilemedi: %s", dedupe_key or title)


def _default_data_dir() -> Path:
    configured = os.environ.get("AGENT_MANAGER_DATA", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "data"


def _read_only(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)


def dry_run(data_dir) -> dict:
    """Inspect persisted state without opening a writable connection.

    No pending operation is converted to 'unknown' here and no work item is
    started or repaired; this is a pure report for ``--dry-run``.
    """
    data_dir = Path(data_dir)
    report = {
        "data_dir": str(data_dir),
        "work_status": {},
        "active_with_run": 0,
        "due_open": 0,
        "waiting_decisions": 0,
        "unknown_operations": [],
        "auto_actions": 0,
        "writes": 0,
    }
    work_db = _read_only(data_dir / "work.sqlite")
    if work_db is not None:
        try:
            rows = work_db.execute(
                "SELECT id,status,run_id,due_at,dependencies,decisions "
                "FROM work_items").fetchall()
        finally:
            work_db.close()
        done = {row[0] for row in rows if row[1] == "done"}
        now = _utcnow()
        for work_id, status, run_id, due_at, dependencies, decisions in rows:
            report["work_status"][status] = report["work_status"].get(status, 0) + 1
            if status == "active" and run_id:
                report["active_with_run"] += 1
            if status == "waiting":
                try:
                    decisions = json.loads(decisions or "[]")
                except ValueError:
                    decisions = []
                report["waiting_decisions"] += sum(
                    1 for decision in decisions if decision.get("status") == "waiting")
            if status == "open":
                try:
                    deps = [str(value) for value in json.loads(dependencies or "[]")]
                except ValueError:
                    deps = []
                due = _parse_aware(due_at)
                if set(deps) <= done and (due is None or due <= now):
                    report["due_open"] += 1

    operation_db = _read_only(data_dir / "operations.sqlite")
    if operation_db is not None:
        operation_db.row_factory = sqlite3.Row
        try:
            rows = operation_db.execute(
                "SELECT id,kind,idempotency_key,status,attempts,created_at "
                "FROM operations WHERE status='unknown' ORDER BY created_at").fetchall()
        finally:
            operation_db.close()
        report["unknown_operations"] = [proposal_for(dict(row)) for row in rows]
    return report


def configure_logging(data_dir, *, filename: str = "daemon.log", level=logging.INFO) -> None:
    """Attach a rotating file handler so crashes are readable after a restart.

    Stdout/stderr redirection alone lost the log whenever the watchdog
    truncated it on the next start, so the daemon owns its own log file.
    """
    from logging.handlers import TimedRotatingFileHandler

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        file_handler = TimedRotatingFileHandler(
            data_dir / filename, when="midnight", backupCount=7, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)
    logging.captureWarnings(True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Piko unattended sürücü çekirdeği")
    parser.add_argument("--dry-run", action="store_true",
                        help="Durumu yazmadan raporla ve çık.")
    parser.add_argument("--data-dir", default="",
                        help="Veri klasörü (varsayılan AGENT_MANAGER_DATA veya ./data).")
    parser.add_argument("--tick-seconds", type=int, default=TICK_SECONDS,
                        help="Tick aralığı (varsayılan 10 sn).")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir()
    configure_logging(data_dir)

    if args.dry_run:
        report = dry_run(data_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        for proposal in report["unknown_operations"]:
            print(f"? {proposal['question']}")
        print("dry-run: hiçbir iş başlatılmadı, hiçbir kayıt değiştirilmedi.")
        return 0

    return asyncio.run(serve(data_dir, tick_seconds=args.tick_seconds))


async def build_driver(data_dir, *, tick_seconds: int = TICK_SECONDS,
                       default_mode: str = "orchestrate", allow_writes: bool = False):
    """Build a headless engine stack plus the daemon; used by the CLI and the app.

    Returns ``(daemon, hooks, stack)``; ``stack`` owns the async context manager
    that must stay open for the daemon's lifetime.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from . import health
    from .app import load_and_discover_models, ROOT
    from .driver_hooks import EngineDriverHooks
    from .engine import Engine
    from .integrations import IntegrationGate
    from .metrics import MetricsStore
    from .notifications import NotificationManager
    from .operations import OperationLedger
    from .providers import Providers
    from .resource_locks import ResourceLocks
    from .store import Store
    from .work_record import WorkRecordManager

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "runs.sqlite")
    await store.open()
    stack = contextlib.AsyncExitStack()
    stack.push_async_callback(store.close)
    try:
        saver = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(data_dir / "checkpoints.sqlite")))
        models = await load_and_discover_models(ROOT)
        engine = Engine(store, saver, Providers(models, ROOT), ROOT)
        work = await stack.enter_async_context(_opened(WorkRecordManager(data_dir / "work.sqlite")))
        notifications = await stack.enter_async_context(
            _opened(NotificationManager(data_dir / "notifications.sqlite")))
        ledger = await stack.enter_async_context(_opened(OperationLedger(data_dir / "operations.sqlite")))
        metrics = await stack.enter_async_context(_opened(MetricsStore(data_dir / "metrics.sqlite")))
        integrations = await stack.enter_async_context(
            _opened(IntegrationGate(data_dir, engine.default_workspace, ledger=ledger)))
        locks = await stack.enter_async_context(_opened(ResourceLocks(data_dir / "locks.sqlite")))
        engine.attach_infrastructure(
            work=work, notifications=notifications, ledger=ledger, metrics=metrics,
            integrations=integrations, resource_locks=locks,
            heartbeat=health.beat, heartbeat_retire=health.retire)
        stack.push_async_callback(engine.close)
        # Headless/watchdog boot uses the same recovery contract as UI boot.
        await engine.recover()
        hooks = EngineDriverHooks(engine, default_mode=default_mode,
                                  allow_writes=allow_writes)
        daemon = DriverDaemon(work, hooks, state_path=data_dir / "driver.sqlite",
                              ledger=ledger, tick_seconds=tick_seconds)
    except BaseException:
        await stack.aclose()
        raise
    return daemon, hooks, stack


@contextlib.asynccontextmanager
async def _opened(manager):
    await manager.open()
    try:
        yield manager
    finally:
        await manager.close()


async def serve(data_dir, *, tick_seconds: int = TICK_SECONDS) -> int:
    daemon, _hooks, stack = await build_driver(data_dir, tick_seconds=tick_seconds)
    try:
        await daemon.run()
    finally:
        await stack.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
