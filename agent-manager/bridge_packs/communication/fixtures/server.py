"""Offline mail + calendar fixture server backed by SQLite.

This is a *stand-in provider*. It exposes the same shape a real IMAP/CalDAV or
provider API would (mailbox summary, today's calendar, draft, send) so swapping
in a live provider later is an adapter change only — never an interface change.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_ACCOUNT = "piko@example.test"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview_sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class FixtureServer:
    """A local, deterministic mail/calendar provider used by tests and demos."""

    def __init__(self, path, *, account: str = DEFAULT_ACCOUNT):
        self.path = Path(path)
        self.account = account
        self.lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    async def open(self) -> "FixtureServer":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS inbox(
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    unread INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS inbox_attachments(
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calendar(
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    attendees TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox(
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    to_addresses TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    preview_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sent_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS outbox_sent ON outbox(sent_at);
                CREATE TABLE IF NOT EXISTS calendar_proposals(
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    attendees TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
            """)
        return self

    async def close(self) -> None:
        return None

    # ----------------------------------------------------------- seed (tests)

    async def seed_message(self, *, sender: str, subject: str, body: str,
                           received_at: str = "", unread: bool = True,
                           attachments=()) -> dict:
        message_id = uuid.uuid4().hex
        received = received_at or _now()
        async with self.lock:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO inbox(id,sender,subject,body,received_at,unread) "
                    "VALUES(?,?,?,?,?,?)",
                    (message_id, sender, subject, body, received, int(unread)))
                for item in attachments:
                    db.execute(
                        "INSERT INTO inbox_attachments(id,message_id,name,bytes,sha256) "
                        "VALUES(?,?,?,?,?)",
                        (uuid.uuid4().hex, message_id, str(item.get("name", "ek")),
                         int(item.get("bytes", 0)),
                         str(item.get("sha256") or _preview_sha(item.get("name", "")))))
        return {"id": message_id, "sender": sender, "subject": subject,
                "received_at": received}

    async def seed_event(self, *, title: str, start_at: str, end_at: str,
                         attendees=()) -> dict:
        event_id = uuid.uuid4().hex
        async with self.lock:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO calendar(id,title,start_at,end_at,attendees) "
                    "VALUES(?,?,?,?,?)",
                    (event_id, title, start_at, end_at,
                     json.dumps(list(attendees), ensure_ascii=False)))
        return {"id": event_id, "title": title, "start_at": start_at}

    # ------------------------------------------------------------- provider API

    async def inbox_summary(self, *, limit: int = 10) -> dict:
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,sender,subject,body,received_at,unread FROM inbox "
                "ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
            unread = db.execute("SELECT COUNT(*) FROM inbox WHERE unread=1").fetchone()[0]
            attachment_rows = db.execute(
                "SELECT message_id, COUNT(*) FROM inbox_attachments GROUP BY message_id"
            ).fetchall()
        attachments = {row[0]: row[1] for row in attachment_rows}
        messages = [{
            "id": row["id"], "sender": row["sender"], "subject": row["subject"],
            "preview": (row["body"] or "").strip().splitlines()[0][:160],
            "received_at": row["received_at"], "unread": bool(row["unread"]),
            "attachments": attachments.get(row["id"], 0),
        } for row in rows]
        return {"account": self.account, "total": total, "unread": unread,
                "messages": messages}

    async def today_calendar(self, *, day: str = "", timezone_name: str = "UTC") -> dict:
        target = day or datetime.now(timezone.utc).date().isoformat()
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,title,start_at,end_at,attendees FROM calendar "
                "WHERE substr(start_at,1,10)=? ORDER BY start_at", (target,)).fetchall()
        events = [{
            "id": row["id"], "title": row["title"], "start_at": row["start_at"],
            "end_at": row["end_at"],
            "attendees": json.loads(row["attendees"] or "[]"),
        } for row in rows]
        return {"day": target, "timezone": timezone_name, "events": events}

    async def create_draft(self, *, to, subject: str, body: str) -> dict:
        """Build a draft object; nothing is stored in the outbox."""
        addresses = _recipients(to)
        preview = _render_preview(subject, body, addresses)
        return {"id": f"draft-{uuid.uuid4().hex[:12]}", "kind": "mail",
                "to": addresses, "subject": subject, "body": body,
                "preview": preview, "preview_sha256": _preview_sha(preview),
                "created_at": _now()}

    async def send(self, *, draft: dict, idempotency_key: str) -> dict:
        """Deliver a draft to the outbox. Replaying the key never duplicates."""
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency key gerekli")
        addresses = _recipients(draft.get("to"))
        if not addresses:
            raise ValueError("alıcı listesi boş olamaz")
        preview = _render_preview(draft.get("subject", ""), draft.get("body", ""),
                                  addresses)
        digest = _preview_sha(preview)
        async with self.lock:
            with self._connect() as db:
                existing = db.execute(
                    "SELECT id,kind,to_addresses,subject,preview_sha256,sent_at "
                    "FROM outbox WHERE idempotency_key=?", (key,)).fetchone()
                if existing:
                    return {"id": existing["id"], "kind": existing["kind"],
                            "recipients": json.loads(existing["to_addresses"]),
                            "subject": existing["subject"],
                            "preview_sha256": existing["preview_sha256"],
                            "sent_at": existing["sent_at"], "duplicate": True}
                message_id = uuid.uuid4().hex
                sent_at = _now()
                db.execute(
                    "INSERT INTO outbox(id,kind,to_addresses,subject,body,"
                    "preview_sha256,idempotency_key,sent_at) VALUES(?,?,?,?,?,?,?,?)",
                    (message_id, str(draft.get("kind") or "mail"),
                     json.dumps(addresses, ensure_ascii=False),
                     str(draft.get("subject", "")), str(draft.get("body", "")),
                     digest, key, sent_at))
        return {"id": message_id, "kind": str(draft.get("kind") or "mail"),
                "recipients": addresses, "subject": str(draft.get("subject", "")),
                "preview_sha256": digest, "sent_at": sent_at, "duplicate": False}

    async def propose_meeting(self, *, title: str, start_at: str, end_at: str,
                              attendees, idempotency_key: str) -> dict:
        addresses = _recipients(attendees)
        if not title.strip():
            raise ValueError("toplantı başlığı gerekli")
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency key gerekli")
        async with self.lock:
            with self._connect() as db:
                existing = db.execute(
                    "SELECT id,created_at FROM calendar_proposals "
                    "WHERE idempotency_key=?", (key,)).fetchone()
                if existing:
                    return {"id": existing["id"], "duplicate": True,
                            "created_at": existing["created_at"],
                            "title": title, "start_at": start_at, "end_at": end_at,
                            "attendees": addresses}
                proposal_id = uuid.uuid4().hex
                created_at = _now()
                db.execute(
                    "INSERT INTO calendar_proposals(id,title,start_at,end_at,"
                    "attendees,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)",
                    (proposal_id, title, start_at, end_at,
                     json.dumps(addresses, ensure_ascii=False), key, created_at))
        return {"id": proposal_id, "duplicate": False, "created_at": created_at,
                "title": title, "start_at": start_at, "end_at": end_at,
                "attendees": addresses}

    async def outbox(self, *, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,kind,to_addresses,subject,preview_sha256,sent_at "
                "FROM outbox ORDER BY sent_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": row["id"], "kind": row["kind"],
                 "recipients": json.loads(row["to_addresses"]),
                 "subject": row["subject"],
                 "preview_sha256": row["preview_sha256"],
                 "sent_at": row["sent_at"]} for row in rows]


def _recipients(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [item.strip() for item in value.replace(";", ",").split(",")]
    addresses = []
    for item in value:
        address = str(item or "").strip()
        if not address:
            continue
        if "@" not in address or " " in address or len(address) > 320:
            raise ValueError(f"geçersiz alıcı adresi: {address}")
        addresses.append(address)
    return list(dict.fromkeys(addresses))


def _render_preview(subject: str, body: str, addresses) -> str:
    lines = [f"Kime: {', '.join(addresses)}", f"Konu: {subject}", "", str(body)]
    return "\n".join(lines).strip()


def tomorrow(tz_hours: int = 3) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
