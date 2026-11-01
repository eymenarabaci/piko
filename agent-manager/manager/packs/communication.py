"""The "communication" reference pack: mail + calendar, offline fixture provider.

Levels
------
``read``  — inbox summary and today's calendar. **This is the only level the
   proactive background flow may call.**
``draft`` — build a mail draft or a meeting proposal. Nothing is delivered and
   nothing is stored in the outbox.
``apply`` — send the approved draft to the fixture provider's outbox (or propose
   the meeting). Requires a single-use approval token; the ledger key is per send,
   so a duplicate send is impossible.

There is deliberately **no** ``dangerous`` level: deleting mail, purging folders
and changing passwords are not defined.

Evidence: draft/message id, recipient list and sha256 of the rendered preview.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_ID = "communication"
EVIDENCE_FORMAT = "communication-evidence"
READ_ONLY_PROACTIVE = frozenset({"read"})
MAX_RECIPIENTS = 50


class CommunicationError(ValueError):
    error_class = "needs-user"


class RetryableCommunicationError(CommunicationError):
    error_class = "retryable"


class ExternalCommunicationError(CommunicationError):
    error_class = "external-down"


def error_payload(exc: Exception) -> dict:
    return {"bridge": BRIDGE_ID,
            "error_class": getattr(exc, "error_class", "needs-user"),
            "message": str(exc)[:800]}


# ------------------------------------------------------------------ provider


def fixture_path(workspace="", *, override: str = "") -> Path:
    """Where the fixture database lives. A live provider would replace this."""
    if override:
        return Path(override)
    if str(workspace or "").strip():
        return Path(workspace).expanduser().resolve() / ".piko" / "communication.sqlite"
    return Path(__file__).resolve().parents[2] / "data" / "communication-fixture.sqlite"


def provider(workspace="", *, override: str = ""):
    """Return an opened fixture provider (the only thing a live swap must change)."""
    from bridge_packs.communication.fixtures import FixtureServer

    path = fixture_path(workspace, override=override or
                        os.environ.get("PIKO_COMM_FIXTURE_DB", ""))
    return FixtureServer(path)


async def _opened(workspace="", *, override: str = ""):
    return await provider(workspace, override=override).open()


# ----------------------------------------------------------------------- read


def _three_line(lines, fallback: str) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    return "\n".join(cleaned[:3]) if cleaned else fallback


async def read_inbox(workspace="", *, limit: int = 10, override: str = "") -> dict:
    server = await _opened(workspace, override=override)
    summary = await server.inbox_summary(limit=limit)
    head = summary["messages"][:3]
    return {
        "bridge": BRIDGE_ID, "action": "inbox", "level": "read",
        "account": summary["account"], "total": summary["total"],
        "unread": summary["unread"], "messages": summary["messages"],
        "summary": _three_line(
            [f"{summary['unread']} okunmamış / {summary['total']} mail",
             *(f"{item['sender']}: {item['subject']}" for item in head)],
            "Gelen kutusu boş."),
    }


async def read_calendar(workspace="", *, day: str = "", override: str = "",
                        timezone_name: str = "UTC") -> dict:
    server = await _opened(workspace, override=override)
    agenda = await server.today_calendar(day=day, timezone_name=timezone_name)
    events = agenda["events"]
    return {
        "bridge": BRIDGE_ID, "action": "calendar", "level": "read",
        "day": agenda["day"], "events": events, "event_count": len(events),
        "summary": _three_line(
            [f"{agenda['day']} · {len(events)} etkinlik",
             *(f"{item['start_at'][11:16]} {item['title']}" for item in events[:2])],
            "Bugün takvimde etkinlik yok."),
    }


async def proactive_read(workspace="", *, override: str = "") -> dict:
    """The proactive background flow's only allowed entry point."""
    inbox = await read_inbox(workspace, limit=5, override=override)
    calendar = await read_calendar(workspace, override=override)
    return {
        "bridge": BRIDGE_ID, "action": "proactive", "level": "read",
        "inbox": {"unread": inbox["unread"], "total": inbox["total"],
                  "messages": inbox["messages"][:3]},
        "calendar": {"day": calendar["day"], "events": calendar["events"]},
        "summary": _three_line([inbox["summary"].splitlines()[0],
                                calendar["summary"].splitlines()[0],
                                "Proaktif akış yalnız okuma yaptı."], "Boş."),
    }


# ---------------------------------------------------------------------- draft


def _validate_recipients(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.replace(";", ",").split(",")]
    addresses = []
    for item in value:
        address = str(item or "").strip()
        if not address:
            continue
        if "@" not in address or " " in address or len(address) > 320:
            raise CommunicationError(f"Güvenli olmayan alıcı adresi: {address}")
        addresses.append(address)
    addresses = list(dict.fromkeys(addresses))
    if len(addresses) > MAX_RECIPIENTS:
        raise CommunicationError("Alıcı sayısı sınırı aşıldı.")
    return addresses


def _preview(subject: str, body: str, recipients) -> str:
    return "\n".join([f"Kime: {', '.join(recipients)}",
                      f"Konu: {subject}", "", str(body)]).strip()


def _preview_sha(preview: str) -> str:
    return hashlib.sha256(preview.encode("utf-8")).hexdigest()


async def build_mail_draft(*, to, subject: str, body: str,
                           workspace: str = "", override: str = "") -> dict:
    recipients = _validate_recipients(to)
    if not recipients:
        raise CommunicationError("Alıcı listesi boş olamaz.")
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    if not subject:
        raise CommunicationError("Konu gerekli.")
    if not body:
        raise CommunicationError("Gövde boş olamaz.")
    server = await _opened(workspace, override=override)
    draft = await server.create_draft(to=recipients, subject=subject, body=body)
    return {"bridge": BRIDGE_ID, "action": "draft", "level": "draft",
            "kind": "mail", "draft": draft, "recipients": recipients,
            "preview": draft["preview"],
            "preview_sha256": draft["preview_sha256"],
            "summary": _three_line([f"Taslak mail · {len(recipients)} alıcı",
                                    f"Konu: {subject}",
                                    f"sha256: {draft['preview_sha256'][:16]}…"],
                                   "Taslak hazır.")}


async def build_meeting_draft(*, title: str, start_at: str, end_at: str,
                              attendees, agenda: str = "",
                              workspace: str = "", override: str = "") -> dict:
    recipients = _validate_recipients(attendees)
    if not recipients:
        raise CommunicationError("Katılımcı listesi boş olamaz.")
    title = str(title or "").strip()
    if not title:
        raise CommunicationError("Toplantı başlığı gerekli.")
    for value in (start_at, end_at):
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise CommunicationError("Toplantı zamanı ISO 8601 olmalı.") from exc
        if parsed.tzinfo is None:
            raise CommunicationError("Toplantı zamanı saat dilimi içermeli.")
    if end_at <= start_at:
        raise CommunicationError("Bitiş zamanı başlangıçtan sonra olmalı.")
    preview = _preview(title, agenda, recipients)
    digest = _preview_sha(preview)
    return {"bridge": BRIDGE_ID, "action": "draft", "level": "draft",
            "kind": "meeting",
            "draft": {"kind": "meeting", "title": title, "start_at": start_at,
                      "end_at": end_at, "attendees": recipients,
                      "agenda": str(agenda or "")},
            "recipients": recipients, "preview": preview,
            "preview_sha256": digest,
            "summary": _three_line([f"Toplantı önerisi · {len(recipients)} katılımcı",
                                    f"{start_at} → {end_at}",
                                    f"sha256: {digest[:16]}…"], "Toplantı taslağı hazır.")}


# ---------------------------------------------------------------------- apply


async def send_mail(*, to, subject: str, body: str, idempotency_key: str,
                    workspace: str = "", override: str = "") -> dict:
    recipients = _validate_recipients(to)
    if not recipients:
        raise CommunicationError("Alıcı listesi boş olamaz.")
    key = str(idempotency_key or "").strip()
    if not key:
        raise CommunicationError("Gönderim için idempotency anahtarı gerekli.")
    server = await _opened(workspace, override=override)
    draft = await server.create_draft(to=recipients, subject=subject, body=body)
    try:
        sent = await server.send(draft=draft, idempotency_key=key)
    except ValueError as exc:
        raise CommunicationError(str(exc)) from exc
    except OSError as exc:
        raise ExternalCommunicationError(f"mailbox-locked: {exc}") from exc
    return {"bridge": BRIDGE_ID, "action": "apply", "level": "apply", "kind": "mail",
            "message_id": sent["id"], "recipients": sent["recipients"],
            "subject": sent["subject"], "preview_sha256": sent["preview_sha256"],
            "sent_at": sent["sent_at"], "duplicate": bool(sent.get("duplicate")),
            "summary": _three_line(
                [f"Gönderildi · {len(sent['recipients'])} alıcı",
                 f"Konu: {sent['subject']}",
                 f"sha256: {sent['preview_sha256'][:16]}…"], "Mail gönderildi.")}


async def send_meeting(*, title: str, start_at: str, end_at: str, attendees,
                       idempotency_key: str, workspace: str = "",
                       override: str = "") -> dict:
    recipients = _validate_recipients(attendees)
    if not recipients:
        raise CommunicationError("Katılımcı listesi boş olamaz.")
    key = str(idempotency_key or "").strip()
    if not key:
        raise CommunicationError("Toplantı için idempotency anahtarı gerekli.")
    server = await _opened(workspace, override=override)
    try:
        proposal = await server.propose_meeting(
            title=title, start_at=start_at, end_at=end_at,
            attendees=recipients, idempotency_key=key)
    except ValueError as exc:
        raise CommunicationError(str(exc)) from exc
    except OSError as exc:
        raise ExternalCommunicationError(f"calendar-unavailable: {exc}") from exc
    preview = _preview(title, "", recipients)
    return {"bridge": BRIDGE_ID, "action": "apply", "level": "apply",
            "kind": "meeting", "message_id": proposal["id"],
            "recipients": recipients, "subject": title,
            "preview_sha256": _preview_sha(preview),
            "sent_at": proposal["created_at"],
            "duplicate": bool(proposal.get("duplicate")),
            "summary": _three_line(
                [f"Toplantı önerisi kaydedildi · {len(recipients)} katılımcı",
                 f"{start_at} → {end_at}",
                 f"id: {proposal['id']}"], "Toplantı kaydedildi.")}


def build_evidence(applied: dict) -> dict:
    return {
        "format": EVIDENCE_FORMAT,
        "bridge": BRIDGE_ID,
        "ok": bool(applied.get("message_id")),
        "kind": applied.get("kind", "mail"),
        "message_id": applied.get("message_id", ""),
        "recipients": list(applied.get("recipients") or []),
        "preview_sha256": applied.get("preview_sha256", ""),
        "subject": applied.get("subject", ""),
        "duplicate": bool(applied.get("duplicate")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


class CommunicationAdapter:
    bridge_id = BRIDGE_ID
    levels = ("read", "draft", "apply")
    read_only_levels = READ_ONLY_PROACTIVE

    async def read(self, workspace="", *, mode: str = "proactive", **kwargs) -> dict:
        if mode == "inbox":
            return await read_inbox(workspace, **kwargs)
        if mode == "calendar":
            return await read_calendar(workspace, **kwargs)
        return await proactive_read(workspace, **kwargs)

    async def draft(self, *, kind: str = "mail", workspace="", **kwargs) -> dict:
        if kind == "meeting":
            return await build_meeting_draft(workspace=workspace, **kwargs)
        return await build_mail_draft(workspace=workspace, **kwargs)

    async def apply(self, *, kind: str = "mail", workspace="", **kwargs) -> dict:
        if kind == "meeting":
            return await send_meeting(workspace=workspace, **kwargs)
        return await send_mail(workspace=workspace, **kwargs)


def adapter() -> CommunicationAdapter:
    return CommunicationAdapter()


def manifest() -> dict:
    path = Path(__file__).resolve().parents[2] / "bridge_packs" / BRIDGE_ID / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def register(registry) -> dict:
    return registry.register(manifest(), replace=True)

MANAGER_PERSONA = {"role": "scheduler",
                   "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}
APPROVER_PERSONA = {"role": "implementer",
                    "bridges": {BRIDGE_ID: {"levels": ["read", "draft", "apply"]}}}
PROACTIVE_PERSONA = {"role": "reviewer",
                     "bridges": {BRIDGE_ID: {"levels": ["read"]}}}
