"""Golden test: kill/restart resume + exactly one recovery question.

Seeds durable state (an in-flight run linked to a work item, and one pending
external operation), then starts the real server twice with a hard taskkill in
between.

Assertions
----------
1. The interrupted run is picked up again after restart and no second run is
   created for the same work item.
2. The work record still points at the same run id (no duplicate work row).
3. The pending external operation becomes ``unknown`` exactly once and produces
   exactly one recovery question, even across a second restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "gold-data"
PORT = 8769
BASE = f"http://127.0.0.1:{PORT}"


def iso():
    return datetime.now(timezone.utc).isoformat()


def http(method, path, payload=None, timeout=10):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode()
    return json.loads(body) if body else {}


def wait_up(deadline=90):
    end = time.time() + deadline
    while time.time() < end:
        try:
            http("GET", "/api/bootstrap", timeout=3)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


async def seed():
    sys.path.insert(0, str(ROOT))
    from manager.operations import OperationLedger
    from manager.store import Store
    from manager.work_record import WorkRecordManager

    DATA.mkdir(parents=True, exist_ok=True)
    ledger = OperationLedger(DATA / "operations.sqlite")
    await ledger.open()
    operation = await ledger.begin("email:send", "gold:unknown:1", {"to": "a@example.test"})
    await ledger.close()

    store = Store(DATA / "runs.sqlite")
    await store.open()
    now = iso()
    run = {
        "id": "a" * 32, "execution_id": "b" * 32, "conversation_id": "a" * 32,
        "prompt": "Altın test işi", "mode": "plan", "planning_level": "fast",
        "planner_model": "auto", "executor_model": "auto",
        "workspace": str(ROOT / "work"), "project_id": "",
        "conversation_run_id": "", "allow_writes": False,
        "timeout_seconds": 60, "workflow_timeout_seconds": 120, "max_parallel": 1,
        "execution_backend": "direct", "status": "running",
        "created_at": now, "updated_at": now, "execution_created_at": now,
        "error": None, "current_steps": [], "plan_revision": 0,
        "orchestrator_answer": "", "orchestrator_messages": [],
        "results": {}, "phase_status": "executing",
        "plan": {"steps": [{"id": "first", "title": "Altın adım", "prompt": "TASK_first",
                            "model_id": "auto", "dependencies": []}]},
        "program_items": [{"title": "Altın test", "goal": "Altın test işi",
                           "acceptance": "", "demo_requested": False, "schedule_hint": ""}],
        "program_index": 0, "program_attempt": 0, "plan_history": [],
        "completed_items": [], "active_goal": "Altın test işi",
    }
    await store.save(run)
    await store.event(run["id"], "seeded", "Altın test için hazırlandı.")
    await store.close()

    work = WorkRecordManager(DATA / "work.sqlite")
    await work.open()
    item = await work.create("Altın test işi", title="Altın test", run_id=run["id"],
                             status="active", next_action="Run sürüyor.")
    await work.close()
    return item["id"], run["id"], operation["id"]


def start_server():
    env = dict(os.environ)
    env["AGENT_MANAGER_DATA"] = str(DATA)
    env["PYTHONUNBUFFERED"] = "1"
    log = open(DATA / "server.log", "a", encoding="utf-8")
    return subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "run.py"),
         "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def hard_kill(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   capture_output=True, text=True)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def check_state(work_id, run_id, phase, *, expect_resumed=False):
    runs = [item for item in http("GET", "/api/runs") if item.get("id") == run_id]
    assert len(runs) == 1, f"{phase}: beklenen tek run, bulunan {len(runs)}"
    assert runs[0]["status"] != "running", f"{phase}: run hâlâ 'running' görünüyor"
    if expect_resumed:
        events = http("GET", f"/api/runs/{run_id}/events?after=0")["events"]
        resumed = [event for event in events
                   if event["kind"] in {"resumed", "planning", "failed", "resource_wait"}]
        assert resumed, f"{phase}: sürücü run'ı yeniden ele almadı"

    detail = http("GET", f"/api/work/{work_id}")
    assert detail["work"]["run_id"] == run_id, f"{phase}: iş kaydı başka run'a bağlanmış"

    recovery = http("GET", "/api/recovery")["questions"]
    assert len(recovery) == 1, f"{phase}: beklenen 1 kurtarma sorusu, bulunan {len(recovery)}"
    assert recovery[0]["operation_id"], f"{phase}: kurtarma sorusunda işlem kimliği yok"
    assert recovery[0]["question"].count("?") == 1, f"{phase}: soru tek değil"

    notes = http("GET", "/api/notifications?include_suppressed=1")["items"]
    unknown_notes = [note for note in notes if "belirsiz" in note["title"].lower()]
    assert len(unknown_notes) == 1, f"{phase}: belirsiz işlem bildirimi {len(unknown_notes)} kez"
    return runs[0]


def main():
    if DATA.exists():
        shutil.rmtree(DATA, ignore_errors=True)
    work_id, run_id, operation_id = asyncio.run(seed())
    print(json.dumps({"phase": "seeded", "work": work_id, "run": run_id,
                      "operation": operation_id}))

    results = {}
    proc = start_server()
    try:
        assert wait_up(), "sunucu ilk açılışta hazır olmadı"
        deadline = time.time() + 90
        while time.time() < deadline:
            stats = http("GET", "/api/driver/status")["stats"]
            detail = http("GET", f"/api/work/{work_id}")
            if stats["resumed"] >= 1 and detail["work"]["status"] != "active":
                break
            time.sleep(1)
        stats = http("GET", "/api/driver/status")["stats"]
        assert stats["resumed"] >= 1, f"restart-1: sürücü run'ı devam ettirmedi: {stats}"
        first = check_state(work_id, run_id, "restart-1", expect_resumed=True)
        results["restart_1_status"] = first["status"]
        results["restart_1_error"] = first.get("error")
        results["driver"] = http("GET", "/api/driver/status")["stats"]
    finally:
        hard_kill(proc)
    print(json.dumps({"phase": "killed", "pid": proc.pid}))

    proc = start_server()
    try:
        assert wait_up(), "sunucu ikinci açılışta hazır olmadı"
        second = check_state(work_id, run_id, "restart-2")
        results["restart_2_status"] = second["status"]
        results["health"] = {"daemon_alive": http("GET", "/api/health")["daemon_alive"]}
        results["driver"] = http("GET", "/api/driver/status")["stats"]
        recovery = http("GET", "/api/recovery")["questions"]
        results["recovery_questions"] = len(recovery)
        results["recovery_dedupe"] = recovery[0]["question"]
    finally:
        hard_kill(proc)

    print(json.dumps({"phase": "done", "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
