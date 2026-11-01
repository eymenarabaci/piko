"""The "izleme" (network/service monitoring) bridge pack: observe, never act.

Levels
------
``read``  - HTTP status + latency probing, TCP port checks, tailing a jsonl
   telemetry/log queue and reading threshold definitions. This is the only
   level the proactive background flow may call, and it is budget-free.
``draft`` - build an alert rule, an incident note and a suggested action, or
   turn a threshold breach into a local event draft. Nothing is delivered to a
   chat, mail, ticket or pager; the draft is an object returned to the caller.

There is deliberately **no** ``apply`` and no ``dangerous`` level: this pack is
pure observation. Sending an alert or auto-remediating belongs to a separate,
approved pack.

Error taxonomy is a first-class distinction:

* ``retryable``     - a transient network blip (timeout / reset). Try again.
* ``external-down`` - the target is unreachable. This is a **finding**, not a
  failure of the monitoring pack itself and must not be raised as an exception.
* ``needs-user``    - the request lacks a usable target/threshold definition.
"""
from __future__ import annotations

import hashlib
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

BRIDGE_ID = "izleme"
EVIDENCE_FORMAT = "izleme-evidence"
READ_ONLY_PROACTIVE = frozenset({"read"})
DEFAULT_SAMPLES = 3
DEFAULT_TIMEOUT = 2.0
DEFAULT_THRESHOLDS = {
    "max_latency_ms": 800.0,
    "expected_status": [200],
    "max_error_count": 0,
}
BUDGET_DEFAULTS = {
    "hourly_request_cap": 120,
    "min_poll_interval_seconds": 10,
}


class MonitoringError(ValueError):
    error_class = "needs-user"


class RetryableMonitoringError(MonitoringError):
    error_class = "retryable"


class ExternalTargetDown(MonitoringError):
    error_class = "external-down"


def error_payload(exc: Exception) -> dict:
    return {
        "bridge": BRIDGE_ID,
        "error_class": getattr(exc, "error_class", "needs-user"),
        "message": str(exc)[:800],
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":"))


def _sha256(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _three_line(lines, fallback: str) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    return "\n".join(cleaned[:3]) if cleaned else fallback


def _positive_int(value, field: str, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MonitoringError(f"ambiguous-target: '{field}' sayi olmali.") from exc
    if number < minimum:
        raise MonitoringError(f"ambiguous-target: '{field}' en az {minimum} olmali.")
    return number


def _samples(value) -> int:
    return max(DEFAULT_SAMPLES, _positive_int(value, "samples"))


def validate_target(target: str):
    text = str(target or "").strip()
    if not text:
        raise MonitoringError("missing-target: izlenecek hedef tanimli degil.")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MonitoringError(
            f"ambiguous-target: '{text}' http(s)://host[/yol] biciminde olmali.")
    return parsed


def normalize_thresholds(value=None) -> dict:
    merged = dict(DEFAULT_THRESHOLDS)
    if value:
        if not isinstance(value, dict):
            raise MonitoringError("missing-thresholds: esik tanimi bir nesne olmali.")
        merged.update(value)
    try:
        merged["max_latency_ms"] = float(merged["max_latency_ms"])
    except (TypeError, ValueError) as exc:
        raise MonitoringError("missing-thresholds: max_latency_ms sayi olmali.") from exc
    try:
        merged["max_error_count"] = int(merged["max_error_count"])
    except (TypeError, ValueError) as exc:
        raise MonitoringError("missing-thresholds: max_error_count sayi olmali.") from exc
    expected = merged.get("expected_status", [200])
    if isinstance(expected, int):
        expected = [expected]
    if not isinstance(expected, (list, tuple, set)):
        raise MonitoringError("missing-thresholds: expected_status liste olmali.")
    merged["expected_status"] = sorted({int(item) for item in expected})
    return merged


def load_thresholds(path) -> dict:
    if not str(path or "").strip():
        raise MonitoringError("missing-thresholds: esik dosyasi yolu gerekli.")
    source = Path(path)
    if not source.exists():
        raise MonitoringError(f"missing-thresholds: '{source}' bulunamadi.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringError(f"missing-thresholds: '{source}' okunamadi.") from exc
    thresholds = normalize_thresholds(payload)
    thresholds["source"] = str(source)
    return thresholds


def _snapshot(**fields) -> dict:
    snapshot = {"bridge": BRIDGE_ID, **fields}
    snapshot["snapshot_sha256"] = _sha256(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"})
    return snapshot


def _classify_probe_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connection-refused", "external-down"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", "retryable"
    if isinstance(exc, httpx.ConnectError):
        return "connection-refused", "external-down"
    if isinstance(exc, httpx.RequestError):
        return "network-error", "retryable"
    return "network-error", "retryable"


async def probe_http(target: str, *, samples: int = DEFAULT_SAMPLES,
                     timeout: float = DEFAULT_TIMEOUT, interval: float = 0.0,
                     thresholds=None, client=None) -> dict:
    validate_target(target)
    count = _samples(samples)
    active_thresholds = normalize_thresholds(thresholds)
    expected = set(active_thresholds["expected_status"])
    own_client = client is None
    http = client or httpx.AsyncClient(follow_redirects=False)
    observations: list[dict] = []
    window_start = _now_iso()
    try:
        for index in range(count):
            if index and interval:
                await _sleep(interval)
            started = time.perf_counter()
            try:
                response = await http.get(str(target), timeout=timeout)
                latency = round((time.perf_counter() - started) * 1000.0, 3)
                observations.append({
                    "index": index,
                    "ok": 200 <= response.status_code < 400,
                    "status_code": response.status_code,
                    "latency_ms": latency,
                    "error": "",
                    "error_class": "",
                })
            except Exception as exc:  # noqa: BLE001 - classified below
                latency = round((time.perf_counter() - started) * 1000.0, 3)
                error, error_class = _classify_probe_error(exc)
                observations.append({
                    "index": index,
                    "ok": False,
                    "status_code": None,
                    "latency_ms": latency,
                    "error": error,
                    "error_class": error_class,
                })
    finally:
        if own_client:
            await http.aclose()
    window_end = _now_iso()

    reachable = [item for item in observations if item["status_code"] is not None]
    codes = [item["status_code"] for item in reachable]
    latencies = [item["latency_ms"] for item in observations]
    if not reachable:
        status = "down"
    elif len(reachable) == count and all(code in expected for code in codes):
        status = "up"
    else:
        status = "degraded"

    error_classes = {item["error_class"] for item in observations if item["error_class"]}
    if not reachable:
        error_class = "external-down" if "external-down" in error_classes else "retryable"
    elif error_classes:
        error_class = "retryable"
    else:
        error_class = ""

    return _snapshot(
        action="probe_http",
        level="read",
        target=str(target),
        status=status,
        reachable=bool(reachable),
        status_codes=codes,
        latency_samples=latencies,
        sample_count=count,
        latency_ms_min=min(latencies) if latencies else 0.0,
        latency_ms_max=max(latencies) if latencies else 0.0,
        latency_ms_avg=round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        window_start=window_start,
        window_end=window_end,
        thresholds=active_thresholds,
        error_class=error_class,
        finding=error_class == "external-down",
        samples=observations,
        summary=_three_line(
            [f"{target} -> {status} ({len(reachable)}/{count} yanit)",
             f"gecikme {min(latencies) if latencies else 0:.1f}-"
             f"{max(latencies) if latencies else 0:.1f} ms",
             f"esik max_latency_ms={active_thresholds['max_latency_ms']:.0f}"],
            "Yoklama tamamlandi."),
    )


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def check_port(host: str, port, *, samples: int = DEFAULT_SAMPLES,
               timeout: float = 1.0) -> dict:
    name = str(host or "").strip()
    if not name:
        raise MonitoringError("missing-target: port kontrolu icin host gerekli.")
    port_number = _positive_int(port, "port")
    if port_number > 65535:
        raise MonitoringError("ambiguous-target: port 1..65535 araliginda olmali.")
    count = _samples(samples)
    observations: list[dict] = []
    window_start = _now_iso()
    for index in range(count):
        started = time.perf_counter()
        opened = False
        error = ""
        try:
            with socket.create_connection((name, port_number), timeout=timeout):
                opened = True
        except OSError as exc:
            error = "connection-refused" if isinstance(exc, ConnectionRefusedError) else "network-error"
        observations.append({
            "index": index,
            "open": opened,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error": error,
        })
    window_end = _now_iso()
    opened_count = sum(1 for item in observations if item["open"])
    status = "up" if opened_count == count else ("degraded" if opened_count else "down")
    latencies = [item["latency_ms"] for item in observations]
    return _snapshot(
        action="check_port",
        level="read",
        target=f"{name}:{port_number}",
        host=name,
        port=port_number,
        open=opened_count == count,
        status=status,
        reachable=opened_count > 0,
        latency_samples=latencies,
        sample_count=count,
        window_start=window_start,
        window_end=window_end,
        error_class="" if opened_count else "external-down",
        finding=opened_count == 0,
        samples=observations,
        summary=_three_line(
            [f"{name}:{port_number} -> {status} ({opened_count}/{count} acik)",
             f"gecikme {min(latencies):.1f}-{max(latencies):.1f} ms"],
            "Port kontrolu tamamlandi."),
    )


def read_telemetry(path, *, limit: int = 20) -> dict:
    if not str(path or "").strip():
        raise MonitoringError("telemetry-missing: telemetri dosyasi yolu gerekli.")
    source = Path(path)
    if not source.exists():
        raise MonitoringError(f"telemetry-missing: '{source}' bulunamadi.")
    limit = _positive_int(limit, "limit")
    records: list[dict] = []
    invalid = 0
    try:
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                invalid += 1
                continue
            records.append(payload if isinstance(payload, dict) else {"value": payload})
    except OSError as exc:
        raise MonitoringError(f"telemetry-missing: '{source}' okunamadi.") from exc
    tail = records[-limit:]
    return {
        "bridge": BRIDGE_ID,
        "action": "telemetry",
        "level": "read",
        "path": str(source),
        "records": tail,
        "count": len(tail),
        "total": len(records),
        "invalid_lines": invalid,
        "summary": _three_line(
            [f"{source.name}: son {len(tail)}/{len(records)} kayit",
             f"gecersiz satir: {invalid}"],
            "Telemetri bos."),
    }


def build_alert_rule(target: str, thresholds=None, *, rule_id: str = "") -> dict:
    validate_target(target)
    active = normalize_thresholds(thresholds)
    identifier = str(rule_id or "").strip() or f"rule-{_sha256({'target': target})[:12]}"
    return {
        "bridge": BRIDGE_ID,
        "action": "alert_rule",
        "level": "draft",
        "rule_id": identifier,
        "target": str(target),
        "conditions": {
            "max_latency_ms": active["max_latency_ms"],
            "expected_status": list(active["expected_status"]),
            "max_error_count": active["max_error_count"],
        },
        "window_samples": DEFAULT_SAMPLES,
        "severity": "critical" if active["max_error_count"] == 0 else "warning",
        "delivery": "none",
        "summary": f"{target} icin alarm kurali taslagi (gonderilmez).",
    }


def detect_threshold_breach(snapshot: dict, thresholds=None) -> list[dict]:
    active = normalize_thresholds(thresholds or snapshot.get("thresholds"))
    breaches: list[dict] = []
    status = str(snapshot.get("status") or "")
    if status == "down":
        breaches.append({"kind": "unreachable", "severity": "critical",
                         "detail": "Hedef yoklamalarda yanit vermedi."})
    elif status == "degraded":
        breaches.append({"kind": "status", "severity": "warning",
                         "detail": f"Beklenen durum disinda: {snapshot.get('status_codes')}"})
    samples = snapshot.get("latency_samples") or []
    if samples:
        worst = max(float(item) for item in samples)
        if worst > active["max_latency_ms"]:
            breaches.append({"kind": "latency", "severity": "warning",
                             "detail": f"{worst:.1f} ms > {active['max_latency_ms']:.1f} ms"})
    if str(snapshot.get("error_class") or "") == "retryable":
        breaches.append({"kind": "transient-network", "severity": "warning",
                         "detail": "Gecici ag kopmasi; tekrar denenebilir."})
    if str(snapshot.get("error_class") or "") == "external-down" and status != "down":
        breaches.append({"kind": "unreachable", "severity": "critical",
                         "detail": "Hedef bazi yoklamalarda ulasilamaz oldu."})
    return breaches


def build_event_note(snapshot: dict, breaches: list[dict], thresholds=None) -> str:
    active = normalize_thresholds(thresholds or snapshot.get("thresholds"))
    details = "; ".join(f"{item['kind']}: {item['detail']}" for item in breaches)
    return _three_line(
        [f"Olay notu: {snapshot.get('target')} -> {snapshot.get('status')}",
         f"Esik: max_latency_ms={active['max_latency_ms']:.0f}, "
         f"expected_status={active['expected_status']}",
         f"Bulgu: {details or 'esik asimi yok'}"],
        "Olay notu bos.")


def propose_action(snapshot: dict, breaches: list[dict]) -> str:
    kinds = {item["kind"] for item in breaches}
    if "unreachable" in kinds:
        return ("Hedefin ag/DNS/firewall erisimini kontrol et; servis surecini ve "
                "son telemetri kayitlarini incele. Otomatik mudahale YAPILMAZ.")
    if "latency" in kinds:
        return ("Gecikme esigi asti; kaynak kullanimini ve bagimli servisleri incele, "
                "gerekirse esik degerini gozden gecir.")
    if "transient-network" in kinds:
        return "Gecici kopma; ayni araligi yeniden yokla ve tekrar ederse eskalasyon taslagi ac."
    if "status" in kinds:
        return ("Hedef beklenen durum kodu disinda yanit verdi; servis loglarini ve "
                "bagimli servisleri incele. Otomatik mudahale YAPILMAZ.")
    return "Durumu izlemeye devam et; esik asimi yok."


def event_fingerprint(target: str, breaches: list[dict], thresholds=None) -> str:
    active = normalize_thresholds(thresholds)
    material = {
        "target": str(target),
        "faults": sorted({item["kind"] for item in breaches}),
        "max_latency_ms": active["max_latency_ms"],
        "expected_status": active["expected_status"],
    }
    return _sha256(material)


class EventDraftBook:
    """Durable duplicate guard so one fault yields exactly one event draft."""

    def __init__(self, path=None) -> None:
        self.path = Path(path) if path else None
        self._seen: dict[str, dict] = {}
        if self.path and self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                self._seen = {str(key): value for key, value in payload.items()
                              if isinstance(value, dict)}

    @property
    def count(self) -> int:
        return len(self._seen)

    def check_and_record(self, fingerprint: str, event: dict) -> tuple[bool, dict]:
        existing = self._seen.get(str(fingerprint))
        if existing is not None:
            return True, existing
        self._seen[str(fingerprint)] = dict(event)
        self._save()
        return False, dict(event)

    def resolve(self, fingerprint: str) -> bool:
        removed = self._seen.pop(str(fingerprint), None) is not None
        if removed:
            self._save()
        return removed

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._seen, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        temporary.replace(self.path)


def draft_alert_event(snapshot: dict, thresholds=None, *, book=None,
                      rule: dict | None = None) -> dict | None:
    active = normalize_thresholds(thresholds or snapshot.get("thresholds"))
    breaches = detect_threshold_breach(snapshot, active)
    if not breaches:
        return None
    target = str(snapshot.get("target") or "")
    fingerprint = event_fingerprint(target, breaches, active)
    event_id = f"evt-{fingerprint[:16]}"
    alert_rule = rule or build_alert_rule(target, active)
    event = {
        "bridge": BRIDGE_ID,
        "action": "event_draft",
        "level": "draft",
        "event_id": event_id,
        "event_key": fingerprint,
        "fingerprint": fingerprint,
        "target": target,
        "status": str(snapshot.get("status") or ""),
        "severity": "critical" if any(item["severity"] == "critical"
                                      for item in breaches) else "warning",
        "breaches": breaches,
        "rule": alert_rule,
        "note": build_event_note(snapshot, breaches, active),
        "proposed_action": propose_action(snapshot, breaches),
        "delivery": "none",
        "sent": False,
        "created_at": _now_iso(),
        "evidence": {
            "target": target,
            "status": snapshot.get("status"),
            "latency_samples": list(snapshot.get("latency_samples") or []),
            "window_start": snapshot.get("window_start"),
            "window_end": snapshot.get("window_end"),
            "threshold": active,
            "snapshot_sha256": snapshot.get("snapshot_sha256"),
        },
        "duplicate": False,
    }
    event["event_sha256"] = _sha256(
        {key: value for key, value in event.items() if key != "event_sha256"})
    if book is not None:
        duplicate, stored = book.check_and_record(fingerprint, event)
        if duplicate:
            stored = dict(stored)
            stored["duplicate"] = True
            return stored
    return event


class BudgetProfile:
    """Hourly request ceiling and minimum poll spacing.

    ``read`` is the proactive observation path and therefore free. ``draft``
    consumes the budget so a noisy incident cannot hammer the target.
    """

    def __init__(self, *, hourly_request_cap=None,
                 min_poll_interval_seconds=None, clock=None) -> None:
        self.hourly_request_cap = int(
            hourly_request_cap if hourly_request_cap is not None
            else BUDGET_DEFAULTS["hourly_request_cap"])
        self.min_poll_interval_seconds = float(
            min_poll_interval_seconds if min_poll_interval_seconds is not None
            else BUDGET_DEFAULTS["min_poll_interval_seconds"])
        self.clock = clock or time.monotonic
        self._draft_stamps: list[float] = []
        self._last_poll: float | None = None

    @classmethod
    def from_dict(cls, value=None, **overrides) -> "BudgetProfile":
        payload = dict(value or {})
        payload.update({key: val for key, val in overrides.items() if val is not None})
        return cls(hourly_request_cap=payload.get("hourly_request_cap"),
                   min_poll_interval_seconds=payload.get("min_poll_interval_seconds"))

    def acquire(self, level: str, *, now=None) -> dict:
        level = str(level or "").strip().lower()
        moment = float(now if now is not None else self.clock())
        if level == "read":
            self._last_poll = moment
            return {"allowed": True, "charged": False, "level": "read",
                    "reason": "proactive-read-free"}
        if level != "draft":
            raise MonitoringError(f"ambiguous-request: '{level}' butce seviyesi degil.")
        horizon = moment - 3600.0
        self._draft_stamps = [stamp for stamp in self._draft_stamps if stamp > horizon]
        if len(self._draft_stamps) >= self.hourly_request_cap:
            return {"allowed": False, "charged": False, "level": "draft",
                    "reason": "hourly-cap", "used": len(self._draft_stamps),
                    "cap": self.hourly_request_cap}
        last = self._draft_stamps[-1] if self._draft_stamps else None
        if last is not None and moment - last < self.min_poll_interval_seconds:
            return {"allowed": False, "charged": False, "level": "draft",
                    "reason": "min-poll-interval",
                    "wait_seconds": round(self.min_poll_interval_seconds - (moment - last), 3)}
        self._draft_stamps.append(moment)
        return {"allowed": True, "charged": True, "level": "draft",
                "used": len(self._draft_stamps), "cap": self.hourly_request_cap}

    def poll_allowed(self, *, now=None) -> bool:
        moment = float(now if now is not None else self.clock())
        return (self._last_poll is None
                or moment - self._last_poll >= self.min_poll_interval_seconds)


class IzlemeAdapter:
    bridge_id = BRIDGE_ID
    levels = ("read", "draft")
    read_only_levels = READ_ONLY_PROACTIVE

    async def read(self, *, mode: str = "probe", **kwargs) -> dict:
        mode = str(mode or "probe").strip().lower()
        if mode in {"probe", "http"}:
            return await probe_http(**kwargs)
        if mode == "port":
            return check_port(**kwargs)
        if mode == "telemetry":
            return read_telemetry(**kwargs)
        if mode == "thresholds":
            return load_thresholds(**kwargs)
        raise MonitoringError(f"ambiguous-request: bilinmeyen read modu '{mode}'.")

    async def draft(self, *, mode: str = "alert_event", **kwargs) -> dict | None:
        mode = str(mode or "alert_event").strip().lower()
        if mode == "alert_rule":
            return build_alert_rule(**kwargs)
        if mode in {"event", "alert_event"}:
            return draft_alert_event(**kwargs)
        if mode == "note":
            snapshot = kwargs.get("snapshot") or {}
            thresholds = kwargs.get("thresholds") or snapshot.get("thresholds")
            breaches = kwargs.get("breaches") or detect_threshold_breach(snapshot, thresholds)
            return {"bridge": BRIDGE_ID, "action": "event_note", "level": "draft",
                    "delivery": "none",
                    "note": build_event_note(snapshot, breaches, thresholds)}
        raise MonitoringError(f"ambiguous-request: bilinmeyen draft modu '{mode}'.")


def adapter() -> IzlemeAdapter:
    return IzlemeAdapter()


def manifest() -> dict:
    path = Path(__file__).resolve().parent / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def register(registry) -> dict:
    return registry.register(manifest(), replace=True)


MANAGER_PERSONA = {"role": "scheduler",
                   "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}
PROACTIVE_PERSONA = {"role": "reviewer",
                     "bridges": {BRIDGE_ID: {"levels": ["read"]}}}
