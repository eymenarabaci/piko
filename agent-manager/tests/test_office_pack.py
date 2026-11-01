"""Contract and end-to-end tests for the reference "office" bridge pack."""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import pytest

from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_bridge_spec, validate_persona_manifest
from manager.engine import Engine
from manager.metrics import MetricsStore
from manager.notifications import NotificationManager
from manager.operations import OperationLedger
from manager.packs import office, office_flow, registry as packs
from manager.resource_locks import ResourceLocks
from manager.store import Store
from manager.work_record import WorkRecordManager

PACK_DIR = Path(__file__).resolve().parents[1] / "bridge_packs" / "office"


class QuietProviders:
    models = [{"id": "worker", "provider": "test", "execution_supported": True}]

    def available(self):
        return [{"id": "test", "available": True, "executable": "test-only"}]

    async def run(self, model_id, prompt, *, workspace, allow_writes, timeout,
                  on_event, effort=""):
        return {"output": "ok", "model_id": model_id, "status": "completed", "usage": {}}


@asynccontextmanager
async def stack(tmp_path):
    store = Store(tmp_path / "runs.sqlite")
    await store.open()
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    notifications = NotificationManager(tmp_path / "notifications.sqlite")
    await notifications.open()
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    metrics = MetricsStore(tmp_path / "metrics.sqlite")
    await metrics.open()
    locks = ResourceLocks(tmp_path / "locks.sqlite")
    await locks.open()
    bridge_gate = BridgeGate(token_store=ledger)
    assert packs.register_shipped(bridge_gate.registry) == ["office", "github"]
    try:
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
            engine = Engine(store, saver, QuietProviders(), tmp_path)
            engine.attach_infrastructure(
                work=work, notifications=notifications, ledger=ledger,
                metrics=metrics, resource_locks=locks, bridge_gate=bridge_gate)
            try:
                yield engine, work, ledger, tmp_path
            finally:
                await engine.close()
    finally:
        await locks.close()
        await metrics.close()
        await ledger.close()
        await notifications.close()
        await work.close()
        await store.close()


def report_request(title="Haftalık durum raporu"):
    return {
        "title": title,
        "sections": [
            {"heading": "Özet", "body": "Sprint hedefleri tamamlandı.",
             "bullets": ["Testler yeşil", "Teslim onaylandı"]},
            {"heading": "Riskler", "body": "Tek risk: model kotası."},
        ],
        "table_headers": ["Görev", "Durum", "Süre"],
        "rows": [["Planlama", "bitti", "2 sa"], ["Uygulama", "bitti", "5 sa"]],
        "formats": ["md", "csv", "xlsx"],
    }


# ------------------------------------------------------------------- contract


def test_manifest_is_a_complete_bridge_contract():
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    spec = validate_bridge_spec(manifest)
    assert spec["id"] == "office"
    assert spec["levels"] == ["read", "draft", "apply"]
    assert set(spec["errors"]) == {"retryable", "needs-user", "external-down"}
    assert spec["evidence"]["format"] == "office-evidence"
    assert set(spec["evidence"]["fields"]) == {"files", "sha256", "summary"}
    assert spec["approval"]["apply"] == "per_call_token"
    assert spec["idempotency"]["type"] == "keyed"


def test_pack_registers_and_exposes_three_levels():
    bridge_registry = BridgeRegistry()
    packs.register_shipped(bridge_registry)
    entry = bridge_registry.get("office")
    assert entry["levels"] == ["read", "draft", "apply"]
    assert bridge_registry.is_loaded("office") is False  # adapters stay lazy


def test_manager_persona_is_read_and_draft_only():
    validated = validate_persona_manifest(office.MANAGER_PERSONA, "scheduler")
    assert validated["bridges"]["office"] == ["read", "draft"]

    bridge_registry = BridgeRegistry()
    bridge_registry.register(office.manifest())
    gate = BridgeGate(bridge_registry)
    gate.check(office.MANAGER_PERSONA, "office", "read")
    gate.check(office.MANAGER_PERSONA, "office", "draft")
    with pytest.raises(PermissionError):
        gate.check(office.MANAGER_PERSONA, "office", "apply",
                   idempotency_key="k1", approval_token="t1")


def test_apply_requires_a_single_use_approval_token():
    bridge_registry = BridgeRegistry()
    bridge_registry.register(office.manifest())
    gate = BridgeGate(bridge_registry)
    with pytest.raises(PermissionError):
        gate.check(office.APPROVER_PERSONA, "office", "apply", idempotency_key="k1")
    gate.check(office.APPROVER_PERSONA, "office", "apply",
               idempotency_key="k1", approval_token="token-once")
    with pytest.raises(PermissionError):
        gate.check(office.APPROVER_PERSONA, "office", "apply",
                   idempotency_key="k2", approval_token="token-once")


# ------------------------------------------------------------------ read level


def test_read_search_and_directory_summary_stay_inside_workspace(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notlar.md").write_text("# Notlar\nkotasız gün\n", encoding="utf-8")
    (tmp_path / "docs" / "veri.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("gizli", encoding="utf-8")

    read = office.read_file(tmp_path, "docs/notlar.md")
    assert read["content"].startswith("# Notlar")
    assert read["sha256"] == hashlib.sha256(
        (tmp_path / "docs" / "notlar.md").read_bytes()).hexdigest()
    assert read["summary"].splitlines()[0] == "# Notlar"

    found = office.search(tmp_path, "kotasız")
    assert [match["path"] for match in found["matches"]] == ["docs/notlar.md"]
    assert found["matches"][0]["line"] == 2

    summary = office.directory_summary(tmp_path, "docs")
    assert summary["file_count"] == 2
    assert summary["by_suffix"][".md"] == 1
    assert len(summary["summary"].splitlines()) == 3

    with pytest.raises(office.OfficeError):
        office.read_file(tmp_path, "../secret.txt")


# ----------------------------------------------------------------- draft level


def test_draft_writes_nothing_and_falls_back_without_xlsx(tmp_path):
    draft = office.build_draft(report_request())
    assert set(draft["files"]) >= {"haftalik-durum-raporu.md", "haftalik-durum-raporu.csv"}
    assert list(tmp_path.iterdir()) == [], "taslak disk yazmamalı"
    assert draft["summary"].splitlines()[0] == "Haftalık durum raporu"
    assert len(draft["summary"].splitlines()) == 3
    assert all(len(value) == 64 for value in draft["hashes"].values())

    try:
        import openpyxl  # noqa: F401
    except Exception:
        assert "haftalik-durum-raporu.xlsx" not in draft["files"]
        assert any("xlsx" in note for note in draft["notes"])


def test_draft_requires_a_title():
    with pytest.raises(office.OfficeError) as error:
        office.build_draft({"sections": [{"heading": "x"}]})
    assert office.error_payload(error.value)["error_class"] == "needs-user"


# ------------------------------------------------------------------ end to end


async def test_office_delivery_flow_end_to_end(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with stack(tmp_path) as (engine, work, ledger, _root):
        item = await work.create("Haftalık raporu hazırla", title="Haftalık rapor",
                                 workspace=str(workspace))
        request = report_request()

        # 1. Manager drafts: read+draft allowed, nothing written; apply is refused
        #    without an approval token even though the caller has read+draft.
        with pytest.raises(PermissionError):
            await office_flow.deliver(
                engine, persona=office.MANAGER_PERSONA, request=request, target="raporlar",
                idempotency_key="office-unapproved", workspace=str(workspace))
        assert not (workspace / "raporlar").exists(), "onaysız apply yazmamalı"
        assert await ledger.list() == [], "onaysız apply deftere yazmamalı"

        # 2. User approves the exact draft; the token is single-use.
        approval = office_flow.issue_approval(office_flow.draft_payload(request))
        assert approval["approval_token"].startswith("office-")
        assert "md" in " ".join(approval["files"])

        first = await office_flow.deliver(
            engine, persona=office.MANAGER_PERSONA, request=request, target="raporlar",
            approval_token=approval["approval_token"],
            idempotency_key="office-delivery-1", work_id=item["id"],
            run_id="run-office-1", workspace=str(workspace))
        assert first["ok"] is True, first
        evidence = first["evidence"]
        assert evidence["format"] == "office-evidence"
        assert evidence["ok"] is True
        assert evidence["files"], "kanıtta dosya listesi olmalı"
        for entry in evidence["files"]:
            written = workspace / entry["path"]
            assert written.is_file()
            assert hashlib.sha256(written.read_bytes()).hexdigest() == entry["sha256"]
            assert entry["sha256"] in evidence["sha256"][entry["path"]]
        assert len(evidence["summary"].splitlines()) == 3
        assert first["decision"]["approval_used"] is True
        assert first["operation"]["status"] == "applied"

        # 3. Delivery record carries the evidence and file list.
        deliverable = first["deliverable"]
        assert deliverable["version"] == 1
        assert deliverable["kind"] == "office-report"
        assert deliverable["files"] == sorted(item2["path"] for item2 in evidence["files"])
        joined = " ".join(deliverable["evidence"])
        assert "sha256" in joined
        assert "özet:" in joined
        stored = await work.get(item["id"])
        assert stored["status"] == "waiting"
        assert deliverable["id"] in stored["outputs"]

        # 4. Replaying the same idempotency key never writes a second version.
        before = (workspace / deliverable["files"][0]).read_text(encoding="utf-8")
        (workspace / deliverable["files"][0]).write_text("elle düzenlendi", encoding="utf-8")
        replay = await office_flow.deliver(
            engine, persona=office.MANAGER_PERSONA, request=request, target="raporlar",
            approval_token="another-token", idempotency_key="office-delivery-1",
            work_id=item["id"], run_id="run-office-1", workspace=str(workspace))
        assert replay["replayed"] is True
        assert (workspace / deliverable["files"][0]).read_text(encoding="utf-8") == "elle düzenlendi"
        versions = await work.list_deliverables(item["id"])
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        operations = await ledger.list()
        assert [operation["status"] for operation in operations] == ["applied"]


async def test_office_refuses_a_target_outside_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    async with stack(tmp_path) as (engine, work, ledger, _root):
        item = await work.create("Sınır testi", workspace=str(workspace))
        request = report_request("Dış hedef")
        approval = office_flow.issue_approval(office_flow.draft_payload(request))
        result = await office_flow.deliver(
            engine, persona=office.MANAGER_PERSONA, request=request,
            target=str(outside), approval_token=approval["approval_token"],
            idempotency_key="office-outside", work_id=item["id"],
            workspace=str(workspace))
        assert result["ok"] is False
        assert result["error"]["error_class"] == "needs-user"
        assert not outside.exists()
        operations = await ledger.list()
        assert operations[0]["status"] == "failed"
        assert await work.find_deliverable(item["id"], "office-outside") is None


def test_error_taxonomy_classes_are_mapped():
    assert office.error_payload(office.RetryableOfficeError("x"))["error_class"] == "retryable"
    assert office.error_payload(office.ExternalOfficeError("x"))["error_class"] == "external-down"
    assert office.error_payload(office.OfficeError("x"))["error_class"] == "needs-user"
