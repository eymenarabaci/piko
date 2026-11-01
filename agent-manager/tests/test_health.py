"""Offline heartbeat/watchdog regressions with a fake clock and tmp SQLite."""

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from manager import health
from manager.health import (
    DAEMON_SOURCE_ID,
    HealthMonitor,
    RUN_KIND,
)

REPORT_KEYS = {
    "generated_at",
    "now",
    "stale_run_seconds",
    "daemon_stale_seconds",
    "daemon",
    "daemon_alive",
    "active_runs",
    "stale_runs",
    "kill_switch",
    "restart_count",
    "restart_window_seconds",
    "restart_limit",
    "last_error",
}


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="health-test-", dir=Path(__file__).parent)
        self.db_path = Path(self.temp.name) / "health.db"
        self.clock = FakeClock()
        self.monitor = HealthMonitor(
            self.db_path,
            clock=self.clock,
            stale_run_seconds=60.0,
            daemon_stale_seconds=300.0,
            restart_window_seconds=600.0,
            restart_limit=3,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_beat_rows_and_health_report_schema(self):
        await self.monitor.beat(DAEMON_SOURCE_ID)
        await self.monitor.beat("run-1", detail="step 2")

        report = self.monitor.report()

        self.assertEqual(REPORT_KEYS, set(report))
        self.assertTrue(report["daemon_alive"])
        self.assertEqual(DAEMON_SOURCE_ID, report["daemon"]["source_id"])
        self.assertEqual(0.0, report["daemon"]["age_seconds"])
        self.assertEqual(["run-1"], [run["source_id"] for run in report["active_runs"]])
        self.assertEqual("step 2", report["active_runs"][0]["detail"])
        self.assertEqual([], report["stale_runs"])
        self.assertFalse(report["kill_switch"])
        self.assertIsNone(report["last_error"])
        self.assertEqual(60.0, report["stale_run_seconds"])

    async def test_stale_boundary_is_strictly_greater_than_threshold(self):
        await self.monitor.beat("run-1", kind=RUN_KIND)

        self.clock.advance(60.0)
        self.assertEqual([], self.monitor.detect_stale())
        self.assertFalse(self.monitor.report()["active_runs"][0]["stale"])

        self.clock.advance(0.5)
        stale = self.monitor.detect_stale()
        self.assertEqual(["run-1"], [run["source_id"] for run in stale])
        self.assertGreater(stale[0]["age_seconds"], 60.0)
        self.assertTrue(stale[0]["stale"])

    async def test_daemon_heartbeat_is_not_reported_as_stale_run(self):
        await self.monitor.beat(DAEMON_SOURCE_ID)
        self.clock.advance(120.0)

        report = self.monitor.report()
        self.assertEqual([], self.monitor.detect_stale())
        self.assertTrue(report["daemon_alive"])

        self.clock.advance(181.0)
        self.assertFalse(self.monitor.report()["daemon_alive"])

    async def test_restart_loop_trips_kill_switch_on_third_restart(self):
        first = self.monitor.record_restart()
        self.clock.advance(30.0)
        second = self.monitor.record_restart()
        self.clock.advance(30.0)
        third = self.monitor.record_restart()

        self.assertEqual([1, 2, 3], [first["count"], second["count"], third["count"]])
        self.assertFalse(first["kill_switch"])
        self.assertFalse(second["kill_switch"])
        self.assertTrue(third["kill_switch"])
        self.assertTrue(self.monitor.kill_switch_active())
        self.assertTrue(self.monitor.report()["kill_switch"])
        self.assertEqual(3, self.monitor.report()["restart_count"])

    async def test_restarts_outside_window_do_not_count(self):
        self.monitor.record_restart()
        self.monitor.record_restart()
        self.monitor.record_restart()
        self.assertTrue(self.monitor.report()["kill_switch"])

        self.clock.advance(601.0)
        report = self.monitor.report()
        self.assertEqual(0, report["restart_count"])
        self.assertFalse(report["kill_switch"])

        fresh = self.monitor.record_restart()
        self.assertEqual(1, fresh["count"])
        self.assertFalse(fresh["kill_switch"])

    async def test_missing_daemon_last_error_and_validation(self):
        report = self.monitor.report()
        self.assertIsNone(report["daemon"])
        self.assertFalse(report["daemon_alive"])
        self.assertEqual([], report["active_runs"])

        self.monitor.record_error("watchdog", "daemon did not come back")
        error = self.monitor.report()["last_error"]
        self.assertEqual("watchdog", error["source_id"])
        self.assertEqual("daemon did not come back", error["message"])
        self.assertTrue(error["recorded_at"].endswith("Z"))

        with self.assertRaises(ValueError):
            self.monitor.record_error("watchdog", "   ")
        with self.assertRaises(ValueError):
            await self.monitor.beat("  ")

    async def test_retire_and_separate_tmp_databases_are_isolated(self):
        other_path = Path(self.temp.name) / "other.db"
        other = HealthMonitor(other_path, clock=self.clock)

        await self.monitor.beat("run-a", kind=RUN_KIND)
        await self.monitor.beat(DAEMON_SOURCE_ID)
        self.monitor.retire("run-a")

        self.assertEqual([], self.monitor.report()["active_runs"])
        self.assertIsNotNone(self.monitor.report()["daemon"])
        self.assertEqual([], other.report()["active_runs"])
        self.assertIsNone(other.report()["daemon"])
        self.assertTrue(self.db_path.exists())
        self.assertTrue(other_path.exists())

        with closing(sqlite3.connect(self.db_path)) as db:
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual("wal", mode.lower())

    async def test_concurrent_beats_use_async_api(self):
        await asyncio.gather(*(self.monitor.beat(f"run-{index}") for index in range(5)))

        report = self.monitor.report()
        self.assertEqual(5, len(report["active_runs"]))
        self.assertEqual([], report["stale_runs"])

    def test_health_module_has_no_network_imports(self):
        source = Path(health.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "import socket",
            "import requests",
            "import httpx",
            "import urllib.request",
            "from urllib import request",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
