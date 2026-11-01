"""Contract and offline end-to-end tests for the "communication" pack.

The provider is the SQLite-backed fixture under ``bridge_packs/communication/
fixtures``. No real mail or calendar API is contacted; swapping in a live
provider is an adapter change, not an interface change.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import pytest

from bridge_packs.communication.fixtures import FixtureServer
from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_bridge_spec, validate_persona_manifest
from manager.engine import Engine
from manager.metrics import MetricsStore
from manager.notifications import NotificationManager
from manager.operations import OperationLedger
from manager.packs import communication, communication_flow, registry as packs
from manager.resource_locks import ResourceLocks
from manager.store import Store
from manager.work_record import WorkRecordManager

PACK_DIR = Path(__file__).resolve().parents[1] / "bridge_packs" / "communication"


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
    gate = BridgeGate(token_store=ledger)
    assert packs.register_shipped(gate.registry) == ["office", "github", "communication"]
    try:
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
            engine = Engine(store, saver, QuietProviders(), tmp_path)
            engine.attach_infrastructure(
                work=work, notifications=notifications, ledger=ledger,
                metrics=metrics, resource_locks=locks, bridge_gate=gate)
            try:
                yield engine, work, ledger
            finally:
                await engine.close()
    finally:
        await locks.close()
        await metrics.close()
        await ledger.close()
        await notifications.close()
        await work.close()
        await store.close()


@pytest.fixture()
async def seeded(tmp_path):
    """Fixture provider with 3 inbox mails, one unread-heavy, and 2 events today."""
    db = tmp_path / "comm.sqlite"
    server = await FixtureServer(db).open()
    today = datetime.now(timezone.utc).replace(microsecond=0)
    await server.seed_message(sender="ayse@example.test", subject="Sprint planı",
                              body="Perşembe 14:00 uygun mu?")
    await server.seed_message(sender="mehmet@example.test", subject="Fatura onayı",
                              body="Ekli faturayı onaylar mısınız?",
                              attachments=[{"name": "fatura.pdf", "bytes": 2048}])
    await server.seed_message(sender="zeynep@example.test", subject="Toplantı notları",
                              body="Notlar ektedir.", unread=False)
    await server.seed_event(title="Günlük standup",
                            start_at=today.replace(hour=9).isoformat(),
                            end_at=today.replace(hour=10).isoformat(),
                            attendees=["ekip@example.test"])
    await server.seed_event(title="Müşteri görüşmesi",
                            start_at=today.replace(hour=15).isoformat(),
                            end_at=today.replace(hour=16).isoformat(),
                            attendees=["musteri@example.test"])
    return db


# ------------------------------------------------------------------- contract


def test_manifest_is_a_complete_contract_without_dangerous():
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    spec = validate_bridge_spec(manifest)
    assert spec["id"] == "communication"
    assert spec["levels"] == ["read", "draft", "apply"]
    assert "dangerous" not in spec["levels"]
    assert "dangerous" not in spec["approval"]
    assert spec["idempotency"] == {"type": "keyed", "key": "idempotency_key"}
    assert spec["evidence"]["format"] == "communication-evidence"
    assert set(spec["evidence"]["fields"]) == {
        "message_id", "recipients", "preview_sha256", "kind"}
    assert spec["approval"]["apply"] == "per_call_token"


def test_error_taxonomy_keeps_send_failures_apart():
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    errors = validate_bridge_spec(manifest)["errors"]
    assert "network-error" in errors["retryable"]
    assert "approval-required" in errors["needs-user"]
    assert "smtp-refused" in errors["external-down"]
    assert not set(errors["retryable"]) & set(errors["external-down"])


def test_error_payloads_map_to_distinct_classes():
    assert communication.error_payload(
        communication.RetryableCommunicationError("x"))["error_class"] == "retryable"
    assert communication.error_payload(
        communication.ExternalCommunicationError("x"))["error_class"] == "external-down"
    assert communication.error_payload(
        communication.CommunicationError("x"))["error_class"] == "needs-user"


def test_manager_persona_is_read_and_draft_only():
    validated = validate_persona_manifest(communication.MANAGER_PERSONA, "scheduler")
    assert validated["bridges"]["communication"] == ["read", "draft"]

    bridge_registry = BridgeRegistry()
    communication.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    gate.check(communication.MANAGER_PERSONA, "communication", "read")
    gate.check(communication.MANAGER_PERSONA, "communication", "draft")
    with pytest.raises(PermissionError):
        gate.check(communication.MANAGER_PERSONA, "communication", "apply",
                   idempotency_key="k1", approval_token="t1")


def test_apply_needs_a_single_use_token():
    bridge_registry = BridgeRegistry()
    communication.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    with pytest.raises(PermissionError):
        gate.check(communication.APPROVER_PERSONA, "communication", "apply",
                   idempotency_key="k1")
    gate.check(communication.APPROVER_PERSONA, "communication", "apply",
               idempotency_key="k1", approval_token="tok-once")
    with pytest.raises(PermissionError):
        gate.check(communication.APPROVER_PERSONA, "communication", "apply",
                   idempotency_key="k2", approval_token="tok-once")


def test_dangerous_level_is_not_defined():
    bridge_registry = BridgeRegistry()
    communication.register(bridge_registry)
    gate = BridgeGate(bridge_registry)
    with pytest.raises(PermissionError):
        gate.check(communication.APPROVER_PERSONA, "communication", "dangerous",
                   idempotency_key="k1", approval_token="t1")


# -------------------------------------------------------- proactive read gate


async def test_proactive_flow_may_only_read(tmp_path, seeded):
    async with stack(tmp_path) as (engine, _work, ledger):
        # read is allowed for the proactive persona.
        decision = await engine.authorize_bridge(
            communication.PROACTIVE_PERSONA, "communication", "read",
            payload={"mode": "proactive"}, proactive=True)
        assert decision["level"] == "read"

        # Every non-read level is refused before the gate/ledger is touched.
        for level in ("draft", "apply", "dangerous"):
            with pytest.raises(PermissionError) as error:
                await engine.authorize_bridge(
                    communication.PROACTIVE_PERSONA, "communication", level,
                    idempotency_key="proactive-1", approval_token="token",
                    payload={}, proactive=True)
            assert "Proaktif akış yalnız" in str(error.value)
        assert await ledger.list() == [], "reddedilen proaktif çağrı deftere yazmamalı"

        # And the data it reads is exactly inbox + calendar.
        snapshot = await communication.proactive_read(override=str(seeded))
        assert snapshot["level"] == "read"
        assert snapshot["inbox"]["unread"] == 2
        assert snapshot["calendar"]["day"]
        assert len(snapshot["summary"].splitlines()) == 3


# ------------------------------------------------------------------ read level


async def test_inbox_summary_and_today_calendar(seeded):
    inbox = await communication.read_inbox(override=str(seeded))
    assert inbox["total"] == 3
    assert inbox["unread"] == 2
    assert inbox["messages"][0]["subject"]
    assert inbox["summary"].splitlines()[0] == "2 okunmamış / 3 mail"
    assert any(item["attachments"] == 1 for item in inbox["messages"])

    calendar = await communication.read_calendar(override=str(seeded))
    assert calendar["event_count"] == 2
    assert {event["title"] for event in calendar["events"]} == {
        "Günlük standup", "Müşteri görüşmesi"}
    assert calendar["summary"].splitlines()[0].endswith("2 etkinlik")


# ----------------------------------------------------------------- draft level


async def test_draft_mail_is_built_without_touching_the_outbox(tmp_path, seeded):
    draft = await communication.build_mail_draft(
        to=["ayse@example.test", "mehmet@example.test"],
        subject="Sprint planı", body="Perşembe 14:00 uygun.",
        override=str(seeded))
    assert draft["kind"] == "mail"
    assert len(draft["preview_sha256"]) == 64
    assert "Kime: ayse@example.test, mehmet@example.test" in draft["preview"]
    server = await FixtureServer(seeded).open()
    assert await server.outbox() == [], "taslak outbox'a yazmamalı"


async def test_draft_meeting_validates_timezone_and_order(seeded):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)
    draft = await communication.build_meeting_draft(
        title="Sprint planlama", start_at=start.isoformat(), end_at=end.isoformat(),
        attendees=["ekip@example.test"], agenda="Backlog",
        override=str(seeded))
    assert draft["kind"] == "meeting"
    assert draft["preview_sha256"]

    with pytest.raises(communication.CommunicationError):
        await communication.build_meeting_draft(
            title="TZ yok", start_at="2026-11-02T10:00:00",
            end_at="2026-11-02T11:00:00", attendees=["a@example.test"],
            override=str(seeded))
    with pytest.raises(communication.CommunicationError):
        await communication.build_meeting_draft(
            title="Ters", start_at=end.isoformat(), end_at=start.isoformat(),
            attendees=["a@example.test"], override=str(seeded))


async def test_draft_rejects_unsafe_recipients(seeded):
    for recipient in ("", "boşluk lu@example.test", "no-at-sign", "a@b c"):
        with pytest.raises(communication.CommunicationError):
            await communication.build_mail_draft(
                to=[recipient], subject="x", body="y", override=str(seeded))


# ------------------------------------------------------------------ end to end


async def test_communication_delivery_flow_end_to_end(tmp_path, seeded):
    async with stack(tmp_path) as (engine, work, ledger):
        item = await work.create("Gelen kutusunu özetle ve yanıt gönder",
                                 title="İletişim görevi", workspace=str(tmp_path))
        request = {
            "kind": "mail",
            "to": ["ayse@example.test", "mehmet@example.test"],
            "subject": "Sprint planı ve fatura",
            "body": "Perşembe 14:00 uygun. Faturayı onaylıyorum.",
        }

        # 1. Proactive read: only read level, three mails visible.
        snapshot = await communication.proactive_read(override=str(seeded))
        assert snapshot["inbox"]["unread"] == 2

        # 2. Manager drafts: read+draft allowed, outbox untouched, ledger empty.
        drafted = await communication_flow.deliver(
            engine, persona=communication.MANAGER_PERSONA, request=request,
            workspace=str(tmp_path), draft_only=True)
        assert drafted["draft_only"] is True
        assert drafted["preview"].startswith("Kime: ayse@example.test")
        assert await ledger.list() == []
        server = await FixtureServer(seeded).open()
        assert await server.outbox() == []

        # 3. Approval binds the exact preview.
        approval = communication_flow.issue_approval(request)
        assert approval["approval_token"].startswith("communication-")
        assert approval["draft_hash"] == drafted["preview_sha256"]
        assert approval["recipients"] == request["to"]

        # 4. Approved delivery: applied in the ledger, message in the outbox.
        delivered = await communication_flow.deliver(
            engine, persona=communication.MANAGER_PERSONA, request=request,
            approval_token=approval["approval_token"],
            idempotency_key="comm-1", work_id=item["id"], run_id="run-comm-1",
            workspace=str(tmp_path), fixture_db=str(seeded))
        assert delivered["ok"] is True, delivered
        evidence = delivered["evidence"]
        assert evidence["format"] == "communication-evidence"
        assert evidence["kind"] == "mail"
        assert evidence["message_id"]
        assert evidence["recipients"] == request["to"]
        assert len(evidence["preview_sha256"]) == 64
        assert delivered["operation"]["status"] == "applied"

        outbox = await server.outbox()
        assert len(outbox) == 1
        assert outbox[0]["id"] == evidence["message_id"]
        assert outbox[0]["recipients"] == request["to"]
        assert outbox[0]["preview_sha256"] == evidence["preview_sha256"]

        deliverable = delivered["deliverable"]
        assert deliverable["version"] == 1
        assert deliverable["kind"] == "communication-message"
        assert deliverable["bridge_id"] == "communication"
        assert evidence["message_id"] in deliverable["files"]
        joined = " ".join(deliverable["evidence"])
        assert "sha256(önizleme)" in joined
        assert "ayse@example.test" in joined

        # 5. Duplicate send is impossible: same key, no second outbox row.
        replay = await communication_flow.deliver(
            engine, persona=communication.MANAGER_PERSONA, request=request,
            approval_token="unused", idempotency_key="comm-1",
            work_id=item["id"], run_id="run-comm-1", workspace=str(tmp_path),
            fixture_db=str(seeded))
        assert replay["replayed"] is True
        assert len(await server.outbox()) == 1
        assert len(await work.list_deliverables(item["id"])) == 1
        assert [op["status"] for op in await ledger.list()] == ["applied"]


async def test_communication_duplicate_key_at_provider_level(seeded):
    """Even bypassing the ledger, the provider itself de-duplicates by key."""
    server = await FixtureServer(seeded).open()
    draft = await server.create_draft(to=["a@example.test"], subject="Tek",
                                      body="Bir kez")
    first = await server.send(draft=draft, idempotency_key="same-key")
    second = await server.send(draft=draft, idempotency_key="same-key")
    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    assert len(await server.outbox()) == 2 - 1  # exactly one row


async def test_communication_apply_without_token_does_not_send(tmp_path, seeded):
    async with stack(tmp_path) as (engine, _work, ledger):
        request = {"kind": "mail", "to": ["ayse@example.test"],
                   "subject": "Onaysız", "body": "gönderilmemeli"}
        with pytest.raises(PermissionError):
            await communication_flow.deliver(
                engine, persona=communication.MANAGER_PERSONA, request=request,
                idempotency_key="comm-denied", workspace=str(tmp_path))
        server = await FixtureServer(seeded).open()
        assert await server.outbox() == []
        assert await ledger.list() == []


async def test_meeting_delivery_end_to_end(tmp_path, seeded):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)
    request = {"kind": "meeting", "title": "Sprint planlama",
               "start_at": start.isoformat(), "end_at": end.isoformat(),
               "attendees": ["ekip@example.test", "ayse@example.test"],
               "agenda": "Backlog önceliklendirme"}
    async with stack(tmp_path) as (engine, work, ledger):
        item = await work.create("Toplantı öner", workspace=str(tmp_path))
        approval = communication_flow.issue_approval(request)
        delivered = await communication_flow.deliver(
            engine, persona=communication.MANAGER_PERSONA, request=request,
            approval_token=approval["approval_token"], idempotency_key="meet-1",
            work_id=item["id"], workspace=str(tmp_path), fixture_db=str(seeded))
        assert delivered["ok"] is True, delivered
        evidence = delivered["evidence"]
        assert evidence["kind"] == "meeting"
        assert evidence["recipients"] == request["attendees"]
        assert delivered["deliverable"]["kind"] == "communication-message"
        assert [op["status"] for op in await ledger.list()] == ["applied"]


async def test_communication_card_is_manifest_driven(tmp_path):
    """All three shipped packs appear without touching per-pack code."""
    from manager.packs import shipped_catalog
    cards = shipped_catalog()
    ids = [entry["bridge"]["id"] for entry in cards]
    assert ids == ["office", "github", "communication"]
    communication_card = next(entry for entry in cards
                              if entry["bridge"]["id"] == "communication")
    assert communication_card["bridge"]["levels"] == ["read", "draft", "apply"]
    assert "dangerous" not in communication_card["bridge"]["levels"]
    assert communication_card["personas"]["manager"]["role"] == "scheduler"



def test_api_lists_three_bridge_cards_and_blocks_proactive_writes(tmp_path):
    """Manifest-driven card list + proactive read-only guard, over HTTP."""
    from fastapi.testclient import TestClient
    from manager.app import create_app

    app = create_app(data_dir=tmp_path / "data", providers=QuietProviders())
    with TestClient(app) as client:
        cards = client.get("/api/bridges/status")
        assert cards.status_code == 200
        ids = [card["bridge"]["id"] for card in cards.json()["packs"]]
        assert ids == ["office", "github", "communication"]
        communication = next(card for card in cards.json()["packs"]
                             if card["bridge"]["id"] == "communication")
        assert communication["bridge"]["levels"] == ["read", "draft", "apply"]
        assert communication["bridge"]["approval"]["apply"] == "per_call_token"
        assert communication["bridge"]["evidence_fields"] == [
            "message_id", "recipients", "preview_sha256", "kind"]

        proactive = client.get("/api/bridges/communication/read", params={"mode": "proactive"})
        assert proactive.status_code == 200
        assert proactive.json()["level"] == "read"

        drafts = client.post("/api/bridges/communication/draft", json={
            "request": {"kind": "mail", "to": ["a@example.test"],
                        "subject": "Deneme", "body": "govde"},
            "workspace": str(tmp_path)})
        assert drafts.status_code == 200
        assert drafts.json()["preview_sha256"]

        without_token = client.post("/api/bridges/communication/deliver", json={
            "request": {"kind": "mail", "to": ["a@example.test"],
                        "subject": "Deneme", "body": "govde"},
            "workspace": str(tmp_path), "idempotency_key": "api-no-token"})
        assert without_token.status_code == 403

        approval = client.post("/api/bridges/communication/approve", json={
            "request": {"kind": "mail", "to": ["a@example.test"],
                        "subject": "Deneme", "body": "govde"}}).json()
        delivered = client.post("/api/bridges/communication/deliver", json={
            "request": {"kind": "mail", "to": ["a@example.test"],
                        "subject": "Deneme", "body": "govde"},
            "workspace": str(tmp_path), "idempotency_key": "api-send-1",
            "approval_token": approval["approval_token"]})
        assert delivered.status_code == 200, delivered.text
        payload = delivered.json()
        assert payload["ok"] is True
        assert payload["evidence"]["recipients"] == ["a@example.test"]
        assert len(payload["evidence"]["preview_sha256"]) == 64
