"""Contract and offline end-to-end tests for the izleme (monitoring) pack.

A stdlib ``http.server`` on an ephemeral port stands in for a real target, so
the suite never touches the network (and never port 8767). The same local
server covers 200 / 500 / slow / crashed scenarios; telemetry is a tmp jsonl
file. The pack only observes: no test expects an outbound alert.
"""
from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bridge_packs.izleme import adapter as izleme
from bridge_packs.izleme import evidence as izleme_evidence
from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_bridge_spec, validate_persona_manifest

PACK_DIR = Path(__file__).resolve().parents[1] / "bridge_packs" / "izleme"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(0.6)
            self._send(200)
            return
        if self.path.startswith("/error"):
            self._send(500)
            return
        if self.path.startswith("/ok"):
            self._send(200)
            return
        self._send(404)

    def _send(self, status: int):
        body = json.dumps({"status": status}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return None


@contextlib.contextmanager
def target_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _manifest() -> dict:
    return json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))


def _registry() -> BridgeRegistry:
    registry = BridgeRegistry()
    registry.register(_manifest())
    return registry


def _persona() -> dict:
    return {"role": "scheduler",
            "bridges": {"izleme": {"levels": ["read", "draft"]}}}


def test_manifest_is_observation_only_and_has_a_reason():
    raw = _manifest()
    spec = validate_bridge_spec(raw)
    assert spec["id"] == "izleme"
    assert spec["levels"] == ["read", "draft"]
    assert "apply" not in spec["levels"]
    assert "dangerous" not in spec["levels"]
    assert spec["approval"] == {}
    assert spec["idempotency"]["type"] == "content-hash"
    assert spec["idempotency"]["key"] == "event_key"
    assert spec["evidence"]["format"] == "izleme-evidence"
    assert spec["evidence"]["fields"] == [
        "target", "status", "latency_samples", "window_start", "window_end",
        "threshold", "snapshot_sha256"]
    assert {"retryable", "needs-user", "external-down"} <= set(spec["errors"])
    assert str(raw.get("reason") or "").strip()
    assert "apply" in raw["reason"] and "dangerous" in raw["reason"]
    assert raw["budget"]["hourly_request_cap"] > 0
    assert raw["budget"]["min_poll_interval_seconds"] > 0
    assert raw["proactive"]["levels"] == ["read"]

    validated = validate_persona_manifest(_persona(), "scheduler")
    assert validated["bridges"]["izleme"] == ["read", "draft"]

    gate = BridgeGate(_registry())
    assert gate.check(_persona(), "izleme", "read")["level"] == "read"
    assert gate.check(_persona(), "izleme", "draft")["level"] == "draft"
    with pytest.raises(PermissionError):
        gate.check(_persona(), "izleme", "apply",
                   idempotency_key="k-1", approval_token="t-1")
    with pytest.raises(PermissionError):
        gate.check(_persona(), "izleme", "dangerous",
                   idempotency_key="k-1", approval_token="t-1")


async def test_http_200_probe_returns_three_samples_and_snapshot_evidence():
    with target_server() as (base, _port):
        snapshot = await izleme.probe_http(base + "/ok", timeout=2.0)
    assert snapshot["status"] == "up"
    assert snapshot["reachable"] is True
    assert snapshot["sample_count"] >= 3
    assert len(snapshot["latency_samples"]) >= 3
    assert set(snapshot["status_codes"]) == {200}
    assert len(snapshot["snapshot_sha256"]) == 64
    assert snapshot["window_start"] <= snapshot["window_end"]

    evidence = izleme_evidence.build_evidence(snapshot)
    assert evidence["format"] == "izleme-evidence"
    assert evidence["target"] == snapshot["target"]
    assert evidence["status"] == "up"
    assert len(evidence["latency_samples"]) >= 3
    assert evidence["threshold"]["max_latency_ms"] == snapshot["thresholds"]["max_latency_ms"]
    assert evidence["snapshot_sha256"] == snapshot["snapshot_sha256"]

    gate = BridgeGate(_registry())
    result = gate.check(_persona(), "izleme", "read")
    assert result["ledger_key"].startswith("izleme:read:")


async def test_http_500_is_a_reachable_degraded_finding():
    with target_server() as (base, _port):
        snapshot = await izleme.probe_http(base + "/error", timeout=2.0)
    assert snapshot["status"] == "degraded"
    assert snapshot["reachable"] is True
    assert set(snapshot["status_codes"]) == {500}
    assert snapshot["error_class"] == ""
    assert snapshot["finding"] is False
    breaches = izleme.detect_threshold_breach(snapshot)
    assert [item["kind"] for item in breaches] == ["status"]


async def test_slow_target_is_retryable_not_external_down():
    with target_server() as (base, _port):
        snapshot = await izleme.probe_http(
            base + "/slow", timeout=0.15, interval=0.0)
    assert snapshot["reachable"] is False
    assert snapshot["status"] == "down"
    assert snapshot["error_class"] == "retryable"
    assert snapshot["finding"] is False
    assert all(item["error"] == "timeout" for item in snapshot["samples"])


async def test_crashed_target_is_an_external_down_finding_not_an_exception():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

    snapshot = await izleme.probe_http(
        f"http://127.0.0.1:{port}/ok", timeout=0.5)
    assert snapshot["status"] == "down"
    assert snapshot["reachable"] is False
    assert snapshot["error_class"] == "external-down"
    assert snapshot["finding"] is True
    assert snapshot["samples"][0]["error"] in {"connection-refused", "network-error"}
    assert izleme.error_payload(izleme.ExternalTargetDown("x"))["error_class"] == "external-down"


def test_port_check_telemetry_tail_and_threshold_file(tmp_path):
    with target_server() as (_base, port):
        opened = izleme.check_port("127.0.0.1", port)
        assert opened["open"] is True
        assert opened["status"] == "up"
        assert len(opened["latency_samples"]) >= 3

    closed = izleme.check_port("127.0.0.1", _free_port())
    assert closed["open"] is False
    assert closed["status"] == "down"
    assert closed["error_class"] == "external-down"

    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text("\n".join([
        json.dumps({"seq": 1, "latency_ms": 11}),
        json.dumps({"seq": 2, "latency_ms": 12}),
        json.dumps({"seq": 3, "latency_ms": 13}),
        json.dumps({"seq": 4, "latency_ms": 14}),
        "{bozuk-json",
    ]) + "\n", encoding="utf-8")
    tail = izleme.read_telemetry(telemetry, limit=3)
    assert tail["count"] == 3
    assert [item["seq"] for item in tail["records"]] == [2, 3, 4]
    assert tail["invalid_lines"] == 1

    threshold_file = tmp_path / "thresholds.json"
    threshold_file.write_text(json.dumps({
        "max_latency_ms": 250, "expected_status": [200, 204],
        "max_error_count": 1}), encoding="utf-8")
    thresholds = izleme.load_thresholds(threshold_file)
    assert thresholds["max_latency_ms"] == 250.0
    assert thresholds["expected_status"] == [200, 204]
    assert thresholds["source"] == str(threshold_file)

    with pytest.raises(izleme.MonitoringError):
        izleme.load_thresholds(tmp_path / "missing.json")
    with pytest.raises(izleme.MonitoringError):
        izleme.read_telemetry(tmp_path / "missing.jsonl")
    with pytest.raises(izleme.MonitoringError):
        izleme.build_alert_rule("")
    assert izleme.error_payload(izleme.MonitoringError("x"))["error_class"] == "needs-user"
    assert izleme.error_payload(izleme.RetryableMonitoringError("x"))["error_class"] == "retryable"


async def test_read_to_threshold_breach_to_event_draft_to_evidence(tmp_path):
    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(json.dumps({"seq": 1, "latency_ms": 999}) + "\n",
                         encoding="utf-8")
    book = izleme.EventDraftBook(tmp_path / "events.json")
    thresholds = {"max_latency_ms": 500, "expected_status": [200]}
    with pytest.raises(izleme.MonitoringError):
        await izleme.probe_http("")

    with target_server() as (base, _port):
        snapshot = await izleme.probe_http(base + "/error", thresholds=thresholds)
    assert snapshot["status"] == "degraded"
    assert izleme.read_telemetry(telemetry)["records"][0]["latency_ms"] == 999

    event = izleme.draft_alert_event(snapshot, thresholds, book=book)
    assert event is not None
    assert event["level"] == "draft"
    assert event["delivery"] == "none"
    assert event["sent"] is False
    assert event["event_key"] == event["fingerprint"]
    assert event["note"].startswith("Olay notu:")
    assert event["proposed_action"]
    assert event["evidence"]["snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert len(event["evidence"]["latency_samples"]) >= 3
    assert event["duplicate"] is False

    replay = izleme.draft_alert_event(snapshot, thresholds, book=book)
    assert replay["duplicate"] is True
    assert replay["event_id"] == event["event_id"]
    assert book.count == 1

    persisted = izleme.EventDraftBook(tmp_path / "events.json")
    assert persisted.count == 1
    again = izleme.draft_alert_event(snapshot, thresholds, book=persisted)
    assert again["duplicate"] is True
    assert persisted.count == 1

    healthy = dict(snapshot, status="up", error_class="", finding=False,
                   latency_samples=[10.0, 11.0, 12.0], thresholds=thresholds)
    assert izleme.draft_alert_event(healthy, thresholds, book=book) is None


async def test_latency_breach_and_budget_profile():
    snapshot = {
        "bridge": "izleme", "target": "http://example.test/health",
        "status": "up", "status_codes": [200],
        "latency_samples": [50.0, 900.0, 120.0],
        "window_start": "t0", "window_end": "t1", "error_class": "",
        "thresholds": {"max_latency_ms": 800},
        "snapshot_sha256": "a" * 64,
    }
    breaches = izleme.detect_threshold_breach(snapshot)
    assert [item["kind"] for item in breaches] == ["latency"]
    event = izleme.draft_alert_event(snapshot)
    assert event["severity"] == "warning"
    assert "esik" in event["note"].lower()

    budget = izleme.BudgetProfile(hourly_request_cap=2,
                                  min_poll_interval_seconds=10)
    assert budget.acquire("read", now=0)["charged"] is False
    assert budget.acquire("read", now=1)["charged"] is False
    assert budget.acquire("draft", now=100)["allowed"] is True
    denied = budget.acquire("draft", now=105)
    assert denied["allowed"] is False
    assert denied["reason"] == "min-poll-interval"
    assert budget.acquire("draft", now=111)["allowed"] is True
    capped = budget.acquire("draft", now=130)
    assert capped["allowed"] is False
    assert capped["reason"] == "hourly-cap"
    assert budget.poll_allowed(now=131) is True
    with pytest.raises(izleme.MonitoringError):
        budget.acquire("apply", now=200)

