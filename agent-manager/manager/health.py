"""Heartbeat, stale-run and restart-loop core for the unattended driver.

Phase 4, UI-free. Storage is a dedicated SQLite database (WAL) so the external
watchdog can inspect health without opening the orchestrator database. Every
timestamp comes from an injectable clock so tests are deterministic and never
need a network or a sleeping process.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "health.db"
HEALTH_DB_ENV = "PIKO_HEALTH_DB"

DAEMON_SOURCE_ID = "daemon"
DAEMON_KIND = "daemon"
RUN_KIND = "run"
KINDS = frozenset({DAEMON_KIND, RUN_KIND})

DEFAULT_STALE_RUN_SECONDS = 60.0
DEFAULT_DAEMON_STALE_SECONDS = 300.0
DEFAULT_RESTART_WINDOW_SECONDS = 600.0
DEFAULT_RESTART_LIMIT = 3


def resolve_db_path() -> Path:
    """Return the configured health database path (watchdog sets the env var)."""
    override = os.environ.get(HEALTH_DB_ENV)
    return Path(override) if override else DEFAULT_DB_PATH


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return float(value)


class HealthMonitor:
    """Owns the health database; safe to instantiate once per process."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        clock: Callable[[], float] | None = None,
        stale_run_seconds: float = DEFAULT_STALE_RUN_SECONDS,
        daemon_stale_seconds: float = DEFAULT_DAEMON_STALE_SECONDS,
        restart_window_seconds: float = DEFAULT_RESTART_WINDOW_SECONDS,
        restart_limit: int = DEFAULT_RESTART_LIMIT,
        initialize: bool = True,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else resolve_db_path()
        self._clock = clock or time.time
        self.stale_run_seconds = _positive(stale_run_seconds, "stale_run_seconds")
        self.daemon_stale_seconds = _positive(daemon_stale_seconds, "daemon_stale_seconds")
        self.restart_window_seconds = _positive(restart_window_seconds, "restart_window_seconds")
        if isinstance(restart_limit, bool) or not isinstance(restart_limit, int) or restart_limit < 1:
            raise ValueError("restart_limit must be an integer >= 1")
        self.restart_limit = restart_limit
        if initialize:
            self.initialize()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create health tables and indexes idempotently."""
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS heartbeats (
                    source_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('daemon', 'run')),
                    last_beat REAL NOT NULL,
                    started_at REAL NOT NULL,
                    detail TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_heartbeats_kind
                    ON heartbeats (kind);

                CREATE TABLE IF NOT EXISTS restarts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    restarted_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_restarts_source_time
                    ON restarts (source_id, restarted_at);

                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    message TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_errors_recorded
                    ON errors (recorded_at);
                """
            )

    @staticmethod
    def _source(source_id: str) -> str:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        return source_id.strip()

    @staticmethod
    def _kind(kind: str | None) -> str | None:
        if kind is None:
            return None
        if kind not in KINDS:
            raise ValueError("kind must be 'daemon' or 'run'")
        return kind

    def _beat_sync(self, source_id: str, kind: str | None = None, detail: str | None = None) -> dict[str, Any]:
        source = self._source(source_id)
        requested_kind = self._kind(kind)
        now = float(self._clock())
        with self._connect() as db:
            row = db.execute("SELECT kind FROM heartbeats WHERE source_id = ?", (source,)).fetchone()
            if requested_kind is not None:
                resolved_kind = requested_kind
            elif row is not None:
                resolved_kind = row["kind"]
            else:
                resolved_kind = DAEMON_KIND if source == DAEMON_SOURCE_ID else RUN_KIND
            db.execute(
                """
                INSERT INTO heartbeats (source_id, kind, last_beat, started_at, detail)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind = excluded.kind,
                    last_beat = excluded.last_beat,
                    detail = COALESCE(excluded.detail, heartbeats.detail)
                """,
                (source, resolved_kind, now, now, detail),
            )
        return {"source_id": source, "kind": resolved_kind, "last_beat": now}

    async def beat(self, source_id: str, *, kind: str | None = None, detail: str | None = None) -> dict[str, Any]:
        """Stamp one heartbeat without blocking the caller's event loop."""
        return await asyncio.to_thread(self._beat_sync, source_id, kind, detail)

    def retire(self, source_id: str) -> None:
        """Drop a finished run (or daemon) from the active heartbeat table."""
        source = self._source(source_id)
        with self._connect() as db:
            db.execute("DELETE FROM heartbeats WHERE source_id = ?", (source,))

    @staticmethod
    def _heartbeat_view(row: sqlite3.Row, now: float, stale_after: float) -> dict[str, Any]:
        age = max(0.0, now - float(row["last_beat"]))
        return {
            "source_id": row["source_id"],
            "kind": row["kind"],
            "last_beat": _iso(float(row["last_beat"])),
            "last_beat_epoch": float(row["last_beat"]),
            "started_at": _iso(float(row["started_at"])),
            "age_seconds": age,
            "stale": age > stale_after,
            "detail": row["detail"],
        }

    def _rows(self, db: sqlite3.Connection, kind: str | None = None) -> list[sqlite3.Row]:
        if kind is None:
            return list(db.execute("SELECT * FROM heartbeats ORDER BY started_at"))
        return list(db.execute("SELECT * FROM heartbeats WHERE kind = ? ORDER BY started_at", (kind,)))

    def active_runs(self) -> list[dict[str, Any]]:
        now = float(self._clock())
        with self._connect() as db:
            rows = self._rows(db, RUN_KIND)
        return [self._heartbeat_view(row, now, self.stale_run_seconds) for row in rows]

    def detect_stale(self, stale_run_seconds: float | None = None) -> list[dict[str, Any]]:
        """Return runs whose heartbeat is older than the threshold (daemon excluded)."""
        threshold = self.stale_run_seconds if stale_run_seconds is None else _positive(
            stale_run_seconds, "stale_run_seconds"
        )
        now = float(self._clock())
        with self._connect() as db:
            rows = self._rows(db, RUN_KIND)
        stale = [self._heartbeat_view(row, now, threshold) for row in rows]
        return [run for run in stale if run["stale"]]

    def _count_restarts(self, db: sqlite3.Connection, source_id: str, now: float) -> int:
        window_start = now - self.restart_window_seconds
        row = db.execute(
            "SELECT COUNT(*) AS count FROM restarts WHERE source_id = ? AND restarted_at >= ?",
            (source_id, window_start),
        ).fetchone()
        return int(row["count"])

    def restart_count(self, source_id: str = DAEMON_SOURCE_ID) -> int:
        source = self._source(source_id)
        now = float(self._clock())
        with self._connect() as db:
            return self._count_restarts(db, source, now)

    def record_restart(self, source_id: str = DAEMON_SOURCE_ID) -> dict[str, Any]:
        """Record a restart and report whether the crash-loop kill switch tripped."""
        source = self._source(source_id)
        now = float(self._clock())
        window_start = now - self.restart_window_seconds
        with self._connect() as db:
            db.execute(
                "DELETE FROM restarts WHERE source_id = ? AND restarted_at < ?",
                (source, window_start),
            )
            db.execute(
                "INSERT INTO restarts (source_id, restarted_at) VALUES (?, ?)",
                (source, now),
            )
            count = self._count_restarts(db, source, now)
        return {
            "source_id": source,
            "count": count,
            "window_seconds": self.restart_window_seconds,
            "limit": self.restart_limit,
            "kill_switch": count >= self.restart_limit,
        }

    def kill_switch_active(self, source_id: str = DAEMON_SOURCE_ID) -> bool:
        return self.restart_count(source_id) >= self.restart_limit

    def record_error(self, source_id: str, message: str) -> dict[str, Any]:
        source = self._source(source_id)
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        now = float(self._clock())
        with self._connect() as db:
            db.execute(
                "INSERT INTO errors (source_id, recorded_at, message) VALUES (?, ?, ?)",
                (source, now, message.strip()),
            )
        return {"source_id": source, "recorded_at": _iso(now), "message": message.strip()}

    def last_error(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM errors ORDER BY recorded_at DESC, id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "source_id": row["source_id"],
            "recorded_at": _iso(float(row["recorded_at"])),
            "message": row["message"],
        }

    def report(self) -> dict[str, Any]:
        """Build the HealthReport dict consumed by the developer screen."""
        now = float(self._clock())
        with self._connect() as db:
            daemon_rows = self._rows(db, DAEMON_KIND)
            run_rows = self._rows(db, RUN_KIND)
            restart_count = self._count_restarts(db, DAEMON_SOURCE_ID, now)
            error_row = db.execute(
                "SELECT * FROM errors ORDER BY recorded_at DESC, id DESC LIMIT 1"
            ).fetchone()

        daemon_row = max(daemon_rows, key=lambda row: float(row["last_beat"]), default=None)
        daemon = (
            self._heartbeat_view(daemon_row, now, self.daemon_stale_seconds)
            if daemon_row is not None
            else None
        )
        active_runs = [self._heartbeat_view(row, now, self.stale_run_seconds) for row in run_rows]
        stale_runs = [run for run in active_runs if run["stale"]]
        last_error = (
            {
                "source_id": error_row["source_id"],
                "recorded_at": _iso(float(error_row["recorded_at"])),
                "message": error_row["message"],
            }
            if error_row is not None
            else None
        )
        return {
            "generated_at": _iso(now),
            "now": now,
            "stale_run_seconds": self.stale_run_seconds,
            "daemon_stale_seconds": self.daemon_stale_seconds,
            "daemon": daemon,
            "daemon_alive": bool(daemon is not None and not daemon["stale"]),
            "active_runs": active_runs,
            "stale_runs": stale_runs,
            "kill_switch": restart_count >= self.restart_limit,
            "restart_count": restart_count,
            "restart_window_seconds": self.restart_window_seconds,
            "restart_limit": self.restart_limit,
            "last_error": last_error,
        }


_default_monitor: HealthMonitor | None = None


def get_default_monitor() -> HealthMonitor:
    """Return the process-wide monitor, following PIKO_HEALTH_DB changes."""
    global _default_monitor
    target = resolve_db_path()
    if _default_monitor is None or _default_monitor.db_path != target:
        _default_monitor = HealthMonitor(target)
    return _default_monitor


# Wiring (see manager/daemon.py and manager/engine.py):
#   - the driver beats once per tick with DAEMON_SOURCE_ID / kind="daemon";
#   - the engine beats with its run id on every drive phase and retires the
#     heartbeat when the run terminates;
#   - the developer screen only calls report()/detect_stale(), never writes.
async def beat(source_id: str, *, kind: str | None = None, detail: str | None = None) -> dict[str, Any]:
    return await get_default_monitor().beat(source_id, kind=kind, detail=detail)


async def retire(source_id: str) -> None:
    await asyncio.to_thread(get_default_monitor().retire, source_id)


def report() -> dict[str, Any]:
    return get_default_monitor().report()


def detect_stale(stale_run_seconds: float | None = None) -> list[dict[str, Any]]:
    return get_default_monitor().detect_stale(stale_run_seconds)


def record_restart(source_id: str = DAEMON_SOURCE_ID) -> dict[str, Any]:
    return get_default_monitor().record_restart(source_id)


def record_error(source_id: str, message: str) -> dict[str, Any]:
    return get_default_monitor().record_error(source_id, message)
