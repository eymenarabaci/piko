"""Faz 6: repair loop, one-click decisions and evidence-backed acceptance."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from manager.daemon import DriverDaemon
from manager.delivery_evidence import inspect
from manager.metrics import MetricsStore
from manager.work_record import WorkRecordManager

SHA = "a" * 64


def _office_lines(path: str = "rapor.md") -> dict:
    return {
        "kind": "office-report",
        "bridge_id": "office",
        "files": [path],
        "evidence": [f"office: 1 dosya, hedef work", f"{path} · sha256 {SHA[:16]}… (12 bayt)",
                     "özet: rapor hazır"],
        "note": "özet: rapor hazır",
    }


class RepairHooks:
    """Driver adapter that turns a repair request into a new delivery version."""

    def __init__(self, work: WorkRecordManager):
        self.work = work
        self.repairs: list[tuple[str, int]] = []
        self.started: list[str] = []
        self.notifications: list[dict] = []
        self._sequence = 0

    async def resume_work(self, work):
        return work.get("run_id")

    async def start_work(self, work):
        self._sequence += 1
        self.started.append(work["id"])
        return f"run-{self._sequence}"

    async def start_repair(self, work, deliverable):
        self._sequence += 1
        run_id = f"repair-{self._sequence}"
        self.repairs.append((work["id"], deliverable["version"]))
        self._pending = (work["id"], run_id, deliverable["version"])
        return run_id

    async def finish_repair(self):
        """Simulate the repair run finishing and filing its new version."""
        work_id, run_id, parent_version = self._pending
        return await self.work.add_deliverable(
            work_id, run_id=run_id,
            summary="Düzeltilmiş rapor",
            parent_version=parent_version,
            idempotency_key=f"repair:{run_id}", **_office_lines("rapor-v2.md"))

    async def notify(self, kind, title, body="", *, work_id="", run_id="",
                     dedupe_key="", severity="normal"):
        self.notifications.append({"kind": kind, "title": title, "work_id": work_id})


async def _open_work(tmp_path) -> WorkRecordManager:
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    return work


async def test_reject_repair_accept_round_end_to_end(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Raporu hazırla", due_at="2020-01-01T00:00:00+00:00")
    v1 = await work.add_deliverable(
        item["id"], run_id="run-1", summary="İlk rapor",
        idempotency_key="deliver-v1", **_office_lines())
    rejected = await work.request_repair(v1["id"], "Başlık eksik")
    assert rejected["status"] == "repair_requested"
    reopened = await work.get(item["id"])
    assert reopened["status"] == "open", "onarım sürücü tarafından başlatılmalı"
    assert reopened["run_id"] == ""

    hooks = RepairHooks(work)
    daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite")
    await daemon.open()
    await daemon.tick()

    assert hooks.repairs == [(item["id"], 1)]
    assert daemon.stats["repairs"] == 1
    await hooks.finish_repair()
    versions = await work.list_deliverables(item["id"])
    assert [entry["version"] for entry in versions] == [1, 2]
    assert versions[1]["parent_version"] == 1, "onarım v+1 olarak bağlanmalı"

    active = await work.get(item["id"])
    assert active["status"] == "waiting", "kullanıcı kararı beklenirken iş waiting kalır"
    assert active["run_id"].startswith("repair-")

    accepted = await work.accept(versions[1]["id"], "iyi")
    assert accepted["status"] == "accepted"
    done = await work.get(item["id"])
    assert done["status"] == "done"

    await daemon.close()
    await work.close()


async def test_other_work_keeps_moving_while_repair_awaits_acceptance(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Onarılacak iş", due_at="2020-01-01T00:00:00+00:00")
    v1 = await work.add_deliverable(
        item["id"], run_id="run-1", summary="İlk", idempotency_key="d1",
        **_office_lines())
    await work.request_repair(v1["id"], "Eksik")
    other = await work.create("Bağımsız iş", due_at="2020-01-01T00:00:00+00:00")

    hooks = RepairHooks(work)
    daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite")
    await daemon.open()
    await daemon.tick()
    await daemon.tick()
    await hooks.finish_repair()

    waiting = await work.get(item["id"])
    assert waiting["status"] == "waiting"
    assert other["id"] in hooks.started or (await work.get(other["id"]))["status"] == "active"
    # A second tick keeps the queue moving even though the repair awaits the user.
    await daemon.tick()
    assert (await work.get(item["id"]))["status"] == "waiting"
    await daemon.close()
    await work.close()


async def test_same_daemon_starts_initial_work_and_two_repair_generations(tmp_path):
    work = await _open_work(tmp_path)
    hooks = RepairHooks(work)
    daemon = DriverDaemon(work, hooks, state_path=tmp_path / "driver.sqlite")
    await daemon.open()
    try:
        item = await work.create("Üç sürümlü rapor")
        await daemon.tick()
        assert hooks.started == [item["id"]]
        first_run = (await work.get(item["id"]))["run_id"]
        current = await work.add_deliverable(item["id"], run_id=first_run,
                                             summary="İlk", idempotency_key="first",
                                             **_office_lines())
        for note in ("Başlığı düzelt", "Tabloyu düzelt"):
            await work.request_repair(current["id"], note)
            await daemon.tick()
            current = await hooks.finish_repair()
        assert [version for _, version in hooks.repairs] == [1, 2]
        assert daemon.stats["repairs"] == 2
        assert current["version"] == 3 and current["parent_version"] == 2
    finally:
        await daemon.close()
        await work.close()


async def test_driver_does_not_overwrite_delivery_from_fast_completed_start(tmp_path):
    work = await _open_work(tmp_path)
    class ImmediateHooks(RepairHooks):
        async def start_work(self, item):
            run_id = await super().start_work(item)
            await self.work.create_for_run({"id": run_id, "prompt": item["goal"]}, work_id=item["id"])
            await self.work.add_deliverable(item["id"], run_id=run_id, summary="Hazır", **_office_lines())
            return run_id
    daemon = DriverDaemon(work, ImmediateHooks(work), state_path=tmp_path / "driver.sqlite")
    await daemon.open()
    try:
        item = await work.create("Hızlı rapor")
        await daemon.tick()
        assert (await work.get(item["id"]))["status"] == "waiting"
    finally:
        await daemon.close()
        await work.close()


def test_evidence_inspection_flags_missing_facts():
    complete = inspect({
        "kind": "github-change", "bridge_id": "github",
        "files": ["piko/dal", "abcdef123456"],
        "evidence": ["github: git-only · branch piko/dal",
                     "commit abcdef123456", "PR https://example.test/pr/1", "test: 3 passed"],
        "note": "", "status": "delivered",
    })
    assert complete["complete"] is True
    assert complete["branch"] == "piko/dal"
    assert "abcdef123456"[:12] in complete["short_hashes"]
    assert complete["links"] == ["https://example.test/pr/1"]

    empty = inspect({"kind": "office-report", "bridge_id": "office",
                     "files": [], "evidence": [], "note": ""})
    assert empty["complete"] is False
    assert {"files", "sha256", "summary"} <= set(empty["missing"])

    communication = inspect({
        "bridge_id": "communication", "files": ["msg-1"],
        "evidence": ["mail: msg-1", "alıcılar: a@example.test",
                     f"sha256(önizleme): {SHA[:16]}…"],
        "note": "", "status": "delivered",
    })
    assert communication["complete"] is True


async def test_accept_is_refused_when_evidence_is_missing(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Kanıtsız iş")
    with pytest.raises(ValueError, match="Kanıt eksik"):
        await work.add_deliverable(item["id"], summary="Kanıtsız", bridge_id="office",
                                   kind="office-report", evidence=[], files=[])
    deliverable = await work.add_deliverable(
        item["id"], summary="Kanıtsız", bridge_id="office",
        kind="office-report", evidence=[], files=[], status="draft")
    with pytest.raises(ValueError) as excinfo:
        await work.accept(deliverable["id"])
    assert "kabul edilemez" in str(excinfo.value)
    loaded = await work.get_deliverable(deliverable["id"])
    assert loaded["status"] == "draft"
    await work.close()


def test_api_gates_acceptance_on_evidence_and_counts_rejections(tmp_path):
    from fastapi.testclient import TestClient
    from manager.app import create_app

    class TinyProviders:
        models = [{"id": "worker", "provider": "test", "execution_supported": True}]

        def available(self):
            return [{"id": "test", "available": True, "executable": "test-only"}]

        async def run(self, model_id, prompt, *, workspace, allow_writes, timeout,
                      on_event, effort=""):
            return {"output": "ok", "model_id": model_id, "status": "completed",
                    "usage": {}}

    app = create_app(data_dir=tmp_path / "data", providers=TinyProviders())
    with TestClient(app) as client:
        work = client.post("/api/work", json={"goal": "Kanıtlı teslim"}).json()
        delivered = client.post(f"/api/work/{work['id']}/deliverables", json={
            "summary": "Rapor hazır",
            "kind": "office-report",
            "bridge_id": "office",
            "files": ["rapor.md"],
            "evidence": [f"rapor.md · sha256 {SHA[:16]}… (5 bayt)"],
            "note": "özet: rapor hazır",
        }).json()
        assert delivered["evidence_view"]["complete"] is True
        accepted = client.post(f"/api/deliverables/{delivered['id']}/accept",
                               json={"note": "iyi"})
        assert accepted.status_code == 200, accepted.text

        bare_work = client.post("/api/work", json={"goal": "Kanıtsız teslim"}).json()
        refused_creation = client.post(f"/api/work/{bare_work['id']}/deliverables", json={
            "summary": "Kanıtsız", "kind": "office-report",
            "bridge_id": "office", "files": [], "evidence": [],
        })
        assert refused_creation.status_code == 400
        from functools import partial
        bare = client.portal.call(partial(app.state.work.add_deliverable, bare_work["id"],
                                          summary="Kanıtsız", kind="office-report", status="draft",
                                          bridge_id="office", files=[], evidence=[]))
        assert inspect(bare)["complete"] is False
        refused = client.post(f"/api/deliverables/{bare['id']}/accept",
                              json={"note": "yok"})
        assert refused.status_code == 400
        assert "kabul edilemez" in refused.json()["detail"]

        rejected = client.post(f"/api/deliverables/{bare['id']}/repair",
                               json={"note": "Başlık eksik"})
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "repair_requested"

        metrics = client.get("/api/metrics", params={"task_kind": "delivery"}).json()
        assert metrics["record_count"] >= 2
        group = metrics["models"][0]
        assert group["user_rejected"] >= 1
        assert group["user_accepted"] >= 1


async def test_rejection_count_reaches_metrics(tmp_path):
    metrics = MetricsStore(tmp_path / "metrics.sqlite")
    await metrics.open()
    # The API records one row per red with repairs=1 / rejected, and the accept
    # row carries the total repair count so Phase 7 can compare approval rounds.
    await metrics.record(work_id="w1", run_id="r1", task_kind="delivery",
                         success=False, repairs=1, reviewer_verdict="repair",
                         user_acceptance="rejected")
    await metrics.record(work_id="w1", run_id="r2", task_kind="delivery",
                         success=True, repairs=1, user_acceptance="accepted")
    summary = await metrics.summary(task_kind="delivery")
    assert summary["record_count"] == 2
    group = summary["models"][0]
    assert group["user_rejected"] == 1
    assert group["user_accepted"] == 1
    assert group["repairs"] == 2
    await metrics.close()
