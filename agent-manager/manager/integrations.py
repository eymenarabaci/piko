"""Shared integration tool layer with separate read, draft and apply permissions.

External send/publish is never implicit: the exact content and target are shown and
must be confirmed before the operation ledger applies the change once.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .operations import OperationLedger, OperationUncertain, require_tool


SERVICES = {
    "local_files": {"label": "Yerel dosyalar", "levels": ["read", "draft", "apply"],
                    "available": True},
    "github": {"label": "GitHub", "levels": ["read", "draft", "apply"],
               "available": False},
    "calendar": {"label": "Takvim ve görevler", "levels": ["read", "draft", "apply"],
                 "available": False},
    "drive": {"label": "Drive / Docs / Sheets", "levels": ["read", "draft", "apply"],
              "available": False},
    "email": {"label": "E-posta", "levels": ["read", "draft", "apply"],
              "available": False},
}


class IntegrationUnavailable(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


class IntegrationGate:
    def __init__(self, data_dir, workspace_root, *, ledger: OperationLedger | None = None):
        self.data_dir = Path(data_dir)
        self.workspace_root = Path(workspace_root).resolve()
        self.drafts_dir = self.data_dir / "drafts"
        self.path = str(self.data_dir / "integrations.sqlite")
        self.ledger = ledger
        self.lock = asyncio.Lock()

    async def open(self):
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS drafts(
                id TEXT PRIMARY KEY,
                service TEXT NOT NULL,
                title TEXT NOT NULL,
                target TEXT NOT NULL,
                workspace TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                confirmation_id TEXT,
                expires_at TEXT,
                applied_at TEXT,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS draft_operation ON drafts(confirmation_id)
                WHERE confirmation_id IS NOT NULL;
        """)
        try:
            await self.db.execute("ALTER TABLE drafts ADD COLUMN workspace TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        await self.db.commit()

    async def close(self):
        await self.db.close()

    def registry(self) -> list[dict]:
        return [{"service": key, **value} for key, value in SERVICES.items()]

    async def read_file(self, relative_path: str, *, role: str = "reviewer",
                        max_bytes: int = 200_000) -> dict:
        require_tool(role, "read")
        target = (self.workspace_root / str(relative_path)).resolve()
        if not _inside(self.workspace_root, target):
            raise PermissionError("Çalışma alanı dışındaki dosya okunamaz.")
        if not target.is_file():
            raise FileNotFoundError("Dosya bulunamadı.")
        if target.stat().st_size > max_bytes:
            raise ValueError("Dosya okuma sınırından büyük.")
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": str(target.relative_to(self.workspace_root)), "content": content,
                "bytes": len(content.encode("utf-8"))}

    DRAFT_COLUMNS = ("id", "service", "title", "target", "workspace", "content",
                     "content_hash", "status", "confirmation_id", "expires_at",
                     "applied_at", "error", "created_at")

    @classmethod
    def _draft(cls, row):
        return dict(zip(cls.DRAFT_COLUMNS, row))

    @classmethod
    def _draft_select(cls):
        return ", ".join(cls.DRAFT_COLUMNS)

    def _root_for(self, draft: dict) -> Path:
        value = str(draft.get("workspace") or "").strip()
        return Path(value).resolve() if value else self.workspace_root

    async def create_draft(self, *, service: str, target: str, content: str,
                           title: str = "", role: str = "implementer",
                           workspace: str = "") -> dict:
        require_tool(role, "draft")
        if service not in SERVICES:
            raise ValueError("Bilinmeyen entegrasyon servisi.")
        target = str(target or "").strip()
        content = str(content or "")
        if not target:
            raise ValueError("Taslak hedefi gerekli.")
        if not content.strip():
            raise ValueError("Boş taslak oluşturulamaz.")
        root = Path(workspace).resolve() if str(workspace or "").strip() else self.workspace_root
        if Path(target).is_absolute() or not _inside(root, (root / target).resolve()):
            raise PermissionError("Hedef seçili çalışma alanı dışında.")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        draft_id = uuid.uuid4().hex
        record = {"id": draft_id, "service": service, "title": str(title or target)[:200],
                  "target": target[:2000], "workspace": str(root), "content": content,
                  "content_hash": content_hash, "status": "draft",
                  "confirmation_id": None, "expires_at": None, "applied_at": None,
                  "error": "", "created_at": _now()}
        (self.drafts_dir / f"{draft_id}.txt").write_text(content, encoding="utf-8")
        async with self.lock:
            await self.db.execute(
                f"INSERT INTO drafts({self._draft_select()}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(record[key] for key in self.DRAFT_COLUMNS))
            await self.db.commit()
        return record

    async def get_draft(self, draft_id: str) -> dict | None:
        async with self.lock:
            async with self.db.execute(
                    f"SELECT {self._draft_select()} FROM drafts WHERE id=?",
                    (draft_id,)) as cur:
                row = await cur.fetchone()
        return self._draft(row) if row else None

    async def request_confirmation(self, draft_id: str, *, ttl_seconds: int = 900) -> dict:
        draft = await self.get_draft(draft_id)
        if draft is None:
            raise ValueError("Taslak bulunamadı.")
        if draft["status"] == "applied":
            raise ValueError("Taslak zaten uygulanmış.")
        confirmation_id = uuid.uuid4().hex
        expires_at = datetime.fromtimestamp(time.time() + max(30, int(ttl_seconds)),
                                             timezone.utc).isoformat()
        async with self.lock:
            await self.db.execute(
                "UPDATE drafts SET confirmation_id=?,expires_at=?,status='awaiting_confirmation' "
                "WHERE id=?", (confirmation_id, expires_at, draft_id))
            await self.db.commit()
        return {"draft_id": draft_id, "confirmation_id": confirmation_id,
                "content": draft["content"], "target": draft["target"],
                "service": draft["service"], "content_hash": draft["content_hash"],
                "expires_at": expires_at,
                "message": "Göndermeden önce içerik ve hedefi birlikte onaylayın."}

    async def confirm_and_apply(self, draft_id: str, confirmation_id: str, *,
                                content_hash: str = "", role: str = "implementer") -> dict:
        require_tool(role, "apply")
        draft = await self.get_draft(draft_id)
        if draft is None:
            raise ValueError("Taslak bulunamadı.")
        if draft["status"] == "applied":
            return dict(draft, applied=True, replayed=True)
        if not draft["confirmation_id"] or draft["confirmation_id"] != confirmation_id:
            raise PermissionError("Onay kimliği eşleşmiyor; içerik ve hedef onaylanmadı.")
        if content_hash and content_hash != draft["content_hash"]:
            raise PermissionError("Onaylanan içerik değişmiş.")
        if not draft["expires_at"] or datetime.fromisoformat(draft["expires_at"]) <= datetime.now(timezone.utc):
            raise PermissionError("Onay süresi dolmuş; içeriği yeniden inceleyin.")
        operation = None
        if self.ledger is not None:
            operation = await self.ledger.begin(
                f"integration:{draft['service']}", f"draft:{draft_id}",
                {"target": draft["target"], "content_hash": draft["content_hash"]})
        if not SERVICES.get(draft["service"], {}).get("available"):
            error = f"{draft['service']} bağlantısı yapılandırılmadı; dış işlem uygulanmadı."
            async with self.lock:
                await self.db.execute(
                    "UPDATE drafts SET status='failed',error=? WHERE id=?", (error, draft_id))
                await self.db.commit()
            if operation and operation.get("should_execute"):
                await self.ledger.fail(operation["id"], error)
            return dict(draft, applied=False, status="failed", error=error)
        if draft["service"] != "local_files":
            raise IntegrationUnavailable("Bu servis için uygulama bağlayıcısı yok.")
        target = (self._root_for(draft) / draft["target"]).resolve()
        if not _inside(self._root_for(draft), target):
            error = "Hedef çalışma alanı dışında; yazma reddedildi."
            if operation and operation.get("should_execute"):
                await self.ledger.fail(operation["id"], error)
            raise PermissionError(error)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(draft["content"], encoding="utf-8")
        async with self.lock:
            await self.db.execute(
                "UPDATE drafts SET status='applied',applied_at=?,error='' WHERE id=?",
                (_now(), draft_id))
            await self.db.commit()
        if operation and operation.get("should_execute"):
            await self.ledger.complete(operation["id"],
                                       {"target": str(target), "bytes": len(draft["content"])})
        return dict(await self.get_draft(draft_id), applied=True, replayed=False)
