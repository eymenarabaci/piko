"""Persistent work records and versioned deliverables, independent of chat length."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone

import aiosqlite

WORK_STATUSES = {"open", "active", "waiting", "blocked", "done", "cancelled"}
WORK_PRIORITIES = {"low", "normal", "high", "urgent"}
DELIVERABLE_STATUSES = {"draft", "delivered", "accepted", "repair_requested",
                        "test_pending", "test_passed", "test_failed"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _iso(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Tarih ISO 8601 biçiminde olmalı.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Tarih saat dilimi içermeli.")
    return parsed.astimezone(timezone.utc).isoformat()


def _json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value.strip()] if value.strip() else []
    if isinstance(value, (tuple, set)):
        value = list(value)
    if not isinstance(value, list):
        raise ValueError("Liste bekleniyor.")
    return [str(item).strip()[:4000] for item in value if str(item).strip()]


def _evidence_list(value) -> list:
    if isinstance(value, dict):
        value = [value]
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return _json_list(value)
    result = []
    for entry in value:
        if isinstance(entry, dict):
            encoded = json.dumps(entry, ensure_ascii=False)
            if len(encoded) > 100_000:
                raise ValueError('Kanıt nesnesi çok büyük.')
            result.append(json.loads(encoded))
        elif str(entry).strip():
            result.append(str(entry).strip()[:4000])
    return result


class WorkRecordManager:
    """One durable record per job: goal, requirements, priority, deadline, dependencies,
    acceptance, deliverables and user decisions. Survives restarts and chat growth."""

    def __init__(self, path):
        self.path = str(path)
        self.lock = asyncio.Lock()

    async def open(self):
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS work_items(
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                run_id TEXT,
                title TEXT NOT NULL,
                goal TEXT NOT NULL,
                requirements TEXT NOT NULL,
                acceptance TEXT NOT NULL,
                priority TEXT NOT NULL,
                due_at TEXT NOT NULL,
                dependencies TEXT NOT NULL,
                workspace TEXT NOT NULL DEFAULT '',
                outputs TEXT NOT NULL,
                decisions TEXT NOT NULL,
                status TEXT NOT NULL,
                next_action TEXT NOT NULL,
                last_result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS work_item_run ON work_items(run_id)
                WHERE run_id IS NOT NULL AND run_id != '';
            CREATE INDEX IF NOT EXISTS work_item_conversation
                ON work_items(conversation_id, created_at);
            CREATE TABLE IF NOT EXISTS deliverables(
                id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                parent_version INTEGER,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                requirements TEXT NOT NULL,
                evidence TEXT NOT NULL,
                open_points TEXT NOT NULL,
                files TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT,
                content_hash TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                bridge_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS deliverable_idempotency
                ON deliverables(work_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
            CREATE INDEX IF NOT EXISTS deliverable_work ON deliverables(work_id, version);
        """)
        try:
            await self.db.execute(
                "ALTER TABLE deliverables ADD COLUMN bridge_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            await self.db.execute(
                "ALTER TABLE work_items ADD COLUMN workspace TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        await self.db.commit()

    async def close(self):
        await self.db.close()

    WORK_COLUMNS = ("id", "conversation_id", "run_id", "title", "goal", "requirements",
                    "acceptance", "priority", "due_at", "dependencies", "workspace",
                    "outputs", "decisions", "status", "next_action", "last_result",
                    "created_at", "updated_at")

    @classmethod
    def _select(cls):
        return ", ".join(cls.WORK_COLUMNS)

    @classmethod
    def _row(cls, row):
        item = dict(zip(cls.WORK_COLUMNS, row))
        for key in ("requirements", "acceptance", "dependencies", "outputs", "decisions"):
            item[key] = json.loads(item[key] or "[]")
        return item

    async def create(self, goal: str, *, title: str = "", conversation_id: str = "",
                     run_id: str = "", requirements=(), acceptance=(), priority: str = "normal",
                     due_at: str = "", dependencies=(), status: str = "open",
                     next_action: str = "", workspace: str = "") -> dict:
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("İş kaydı için hedef gerekli.")
        if len(goal) > 8000:
            raise ValueError("Hedef 8000 karakterden uzun olamaz.")
        if priority not in WORK_PRIORITIES:
            raise ValueError("Geçersiz öncelik.")
        if status not in WORK_STATUSES:
            raise ValueError("Geçersiz iş durumu.")
        item = {
            "id": uuid.uuid4().hex,
            "conversation_id": str(conversation_id or uuid.uuid4().hex),
            "run_id": str(run_id or ""),
            "title": str(title or goal)[:160],
            "goal": goal,
            "requirements": _json_list(requirements),
            "acceptance": _json_list(acceptance),
            "priority": priority,
            "due_at": _iso(due_at),
            "dependencies": _json_list(dependencies),
            "workspace": str(workspace or "")[:1000],
            "outputs": [],
            "decisions": [],
            "status": status,
            "next_action": str(next_action)[:600],
            "last_result": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        async with self.lock:
            await self.db.execute(
                f"INSERT INTO work_items({self._select()}) VALUES({','.join('?' for _ in self.WORK_COLUMNS)})",
                tuple(item[key] if key not in {"requirements", "acceptance", "dependencies",
                                               "outputs", "decisions"}
                      else json.dumps(item[key], ensure_ascii=False)
                      for key in self.WORK_COLUMNS))
            await self.db.commit()
        return item

    async def create_for_run(self, run: dict, *, work_id: str = "") -> dict:
        run_id = str(run.get("id") or "")
        existing = await self.by_run(run_id) if run_id else None
        if existing:
            return existing
        if work_id:
            item = await self.get(work_id)
            if item is None:
                raise ValueError("İş kaydı bulunamadı.")
            async with self.lock:
                await self.db.execute(
                    "UPDATE work_items SET run_id=?,status='active',updated_at=? WHERE id=?",
                    (run_id or None, _now(), work_id))
                await self.db.commit()
            return await self.get(work_id)
        program = run.get("program_items") or []
        return await self.create(
            run.get("prompt", ""),
            title=(program[0].get("title") if program else "") or run.get("prompt", ""),
            conversation_id=run.get("conversation_id") or run_id,
            run_id=run_id,
            requirements=[item.get("goal", "") for item in program if item.get("goal")],
            acceptance=[item.get("acceptance", "") for item in program if item.get("acceptance")],
            status="active",
            next_action="Plan hazırlanıyor.",
        )

    async def get(self, work_id: str) -> dict | None:
        async with self.lock:
            async with self.db.execute(f"SELECT {self._select()} FROM work_items WHERE id=?", (work_id,)) as cur:
                row = await cur.fetchone()
        return self._row(row) if row else None

    async def by_run(self, run_id: str) -> dict | None:
        if not run_id:
            return None
        async with self.lock:
            async with self.db.execute(f"SELECT {self._select()} FROM work_items WHERE run_id=?", (run_id,)) as cur:
                row = await cur.fetchone()
        return self._row(row) if row else None

    async def list(self, *, status: str = "", conversation_id: str = "", limit: int = 100):
        clauses, params = [], []
        if status:
            if status not in WORK_STATUSES:
                raise ValueError("Geçersiz iş durumu.")
            clauses.append("status=?")
            params.append(status)
        if conversation_id:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        async with self.lock:
            async with self.db.execute(
                    f"SELECT {self._select()} FROM work_items {where} ORDER BY created_at DESC LIMIT ?",
                    params) as cur:
                rows = await cur.fetchall()
        return [self._row(row) for row in rows]

    async def dependency_index(self) -> dict[str, dict]:
        """Complete {id: {"status", "dependencies"}} index, not window-bound.

        The driver's dependency gate must know every work id: a dependency
        completed long ago is still "done", and a missing or cyclic dependency
        can be told apart from a merely old one.
        """
        async with self.lock:
            async with self.db.execute(
                    "SELECT id, status, dependencies FROM work_items") as cur:
                rows = await cur.fetchall()
        return {row[0]: {"status": row[1],
                         "dependencies": json.loads(row[2] or "[]")}
                for row in rows}

    async def update(self, work_id: str, **fields) -> dict:
        allowed = {"title", "goal", "priority", "due_at", "status", "next_action",
                   "last_result", "requirements", "acceptance", "dependencies", "run_id",
                   "workspace"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Güncellenemeyen alan: {', '.join(sorted(unknown))}")
        if "priority" in fields and fields["priority"] not in WORK_PRIORITIES:
            raise ValueError("Geçersiz öncelik.")
        if "status" in fields and fields["status"] not in WORK_STATUSES:
            raise ValueError("Geçersiz iş durumu.")
        if "due_at" in fields:
            fields["due_at"] = _iso(fields["due_at"])
        for key in ("requirements", "acceptance", "dependencies"):
            if key in fields:
                fields[key] = json.dumps(_json_list(fields[key]), ensure_ascii=False)
        if "title" in fields:
            fields["title"] = str(fields["title"])[:160]
        if "goal" in fields:
            fields["goal"] = str(fields["goal"])[:8000]
        if "next_action" in fields:
            fields["next_action"] = str(fields["next_action"])[:600]
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        async with self.lock:
            await self.db.execute(f"UPDATE work_items SET {assignments} WHERE id=?",
                                  (*fields.values(), work_id))
            await self.db.commit()
        item = await self.get(work_id)
        if item is None:
            raise ValueError("İş kaydı bulunamadı.")
        return item

    async def add_decision(self, work_id: str, question: str, *, options=()) -> dict:
        question = str(question or "").strip()
        if not question:
            raise ValueError("Karar sorusu gerekli.")
        item = await self.get(work_id)
        if item is None:
            raise ValueError("İş kaydı bulunamadı.")
        decisions = list(item["decisions"])
        decision = {"id": uuid.uuid4().hex, "question": question[:1500],
                    "options": _json_list(options), "answer": "", "status": "waiting",
                    "created_at": _now(), "answered_at": ""}
        decisions.append(decision)
        await self.update(work_id, status="waiting", next_action=question[:600])
        async with self.lock:
            await self.db.execute(
                "UPDATE work_items SET decisions=?,updated_at=? WHERE id=?",
                (json.dumps(decisions, ensure_ascii=False), _now(), work_id))
            await self.db.commit()
        return decision

    async def answer_decision(self, work_id: str, decision_id: str, answer: str) -> dict:
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("Karar yanıtı gerekli.")
        item = await self.get(work_id)
        if item is None:
            raise ValueError("İş kaydı bulunamadı.")
        decisions = list(item["decisions"])
        for decision in decisions:
            if decision["id"] == decision_id:
                decision.update(answer=answer[:4000], status="answered", answered_at=_now())
                break
        else:
            raise ValueError("Karar bulunamadı.")
        async with self.lock:
            await self.db.execute(
                "UPDATE work_items SET decisions=?,status='active',next_action=?,updated_at=? WHERE id=?",
                (json.dumps(decisions, ensure_ascii=False), "", _now(), work_id))
            await self.db.commit()
        return await self.get(work_id)

    async def add_output(self, work_id: str, output_id: str) -> None:
        item = await self.get(work_id)
        if item is None:
            raise ValueError("İş kaydı bulunamadı.")
        outputs = list(item["outputs"])
        if output_id not in outputs:
            outputs.append(output_id)
        async with self.lock:
            await self.db.execute(
                "UPDATE work_items SET outputs=?,updated_at=? WHERE id=?",
                (json.dumps(outputs, ensure_ascii=False), _now(), work_id))
            await self.db.commit()

    async def summary(self, work_id: str) -> dict:
        item = await self.get(work_id)
        if item is None:
            raise ValueError("İş kaydı bulunamadı.")
        deliverables = await self.list_deliverables(work_id)
        latest = deliverables[-1] if deliverables else None
        return {
            "work": item,
            "what_was_asked": item["goal"],
            "what_was_done": (latest or {}).get("summary") or item["last_result"] or "",
            "next_action": item["next_action"] or ("Teslimi incele." if latest else "Planı başlat."),
            "status": item["status"],
            "deliverable_count": len(deliverables),
            "latest_version": (latest or {}).get("version"),
        }

    @staticmethod
    def _deliverable(row):
        keys = ("id", "work_id", "run_id", "version", "parent_version", "title", "kind",
                "summary", "requirements", "evidence", "open_points", "files", "status",
                "idempotency_key", "content_hash", "note", "bridge_id", "created_at",
                "updated_at")
        item = dict(zip(keys, row))
        for key in ("requirements", "evidence", "open_points", "files"):
            item[key] = json.loads(item[key] or "[]")
        return item

    async def add_deliverable(self, work_id: str, *, run_id: str = "", title: str = "",
                              kind: str = "report", summary: str = "", requirements=(),
                              evidence=(), open_points=(), files=(), status: str = "delivered",
                              idempotency_key: str = "", parent_version: int | None = None,
                              note: str = "", bridge_id: str = "") -> dict:
        work = await self.get(work_id)
        if work is None:
            raise ValueError("İş kaydı bulunamadı.")
        if status not in DELIVERABLE_STATUSES:
            raise ValueError("Geçersiz teslim durumu.")
        if status == "accepted":
            raise ValueError("Kabul yalnız güncel teslim kabul kapısından verilebilir.")
        summary = str(summary or "").strip()
        if not summary:
            raise ValueError("Teslim için kısa sonuç özeti gerekli.")
        evidence = _evidence_list(evidence)
        payload = json.dumps([title, kind, summary, list(requirements), evidence,
                              list(open_points), list(files)], ensure_ascii=False)
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        idempotency_key = str(idempotency_key or "").strip() or None
        async with self.lock:
            if idempotency_key:
                async with self.db.execute(
                        "SELECT * FROM deliverables WHERE work_id=? AND idempotency_key=?",
                        (work_id, idempotency_key)) as cur:
                    existing = await cur.fetchone()
                if existing:
                    return self._deliverable(existing)
            async with self.db.execute(
                    "SELECT COALESCE(MAX(version),0) FROM deliverables WHERE work_id=?",
                    (work_id,)) as cur:
                row = await cur.fetchone()
            version = int(row[0]) + 1
            if parent_version is None and version > 1:
                parent_version = version - 1
            if ((version == 1 and parent_version is not None) or
                    (version > 1 and (isinstance(parent_version, bool) or parent_version != version - 1))):
                raise ValueError("Teslim yalnız bu işin en güncel sürümünden türeyebilir.")
            if run_id and work.get("run_id") and str(run_id) != work["run_id"]:
                raise ValueError("Teslim güncel iş çalışmasına ait değil.")
            item = {
                "id": uuid.uuid4().hex, "work_id": work_id, "run_id": str(run_id or ""),
                "version": version, "parent_version": parent_version,
                "title": str(title or f"Teslim v{version}")[:200], "kind": str(kind)[:40],
                "summary": summary[:8000], "requirements": _json_list(requirements),
                "evidence": evidence, "open_points": _json_list(open_points),
                "files": _json_list(files), "status": status,
                "idempotency_key": idempotency_key, "content_hash": content_hash,
                "note": str(note)[:2000], "bridge_id": str(bridge_id or "")[:60],
                "created_at": _now(), "updated_at": _now(),
            }
            if status in {"delivered", "test_passed"}:
                from .delivery_evidence import inspect
                view = inspect(item)
                if not view["complete"]:
                    raise ValueError("Kanıt eksik; teslim yayımlanamaz: " + ", ".join(view["missing"]))
            await self.db.execute(
                "INSERT INTO deliverables VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], work_id, item["run_id"], version, parent_version, item["title"],
                 item["kind"], item["summary"],
                 json.dumps(item["requirements"], ensure_ascii=False),
                 json.dumps(item["evidence"], ensure_ascii=False),
                 json.dumps(item["open_points"], ensure_ascii=False),
                 json.dumps(item["files"], ensure_ascii=False), status, idempotency_key,
                 content_hash, item["note"], item["bridge_id"], item["created_at"],
                 item["updated_at"]))
            await self.db.commit()
        await self.add_output(work_id, item["id"])
        await self.update(work_id, status="waiting",
                          next_action="Teslimi incele, kabul et veya düzeltme iste.",
                          last_result=summary[:2000])
        return await self.get_deliverable(item["id"])

    async def list_deliverables(self, work_id: str) -> list[dict]:
        async with self.lock:
            async with self.db.execute(
                    "SELECT * FROM deliverables WHERE work_id=? ORDER BY version", (work_id,)) as cur:
                rows = await cur.fetchall()
        work = await self.get(work_id)
        records = [self._deliverable(row) for row in rows]
        latest = max((item["version"] for item in records), default=0)
        return [self._accept_metadata(item, work or {}, latest) for item in records]

    @staticmethod
    def _accept_reason(item: dict, work: dict, latest: int) -> str:
        if item["version"] != latest:
            return "Yalnız en güncel teslim kabul edilebilir."
        if item["status"] not in {"delivered", "test_passed", "accepted"}:
            return "Teslim bu durumdayken kabul edilemez."
        if work.get("status") not in {"waiting", "done"}:
            return "İşin güncel çalışması veya düzeltmesi henüz tamamlanmadı."
        if work.get("run_id") and item.get("run_id") != work["run_id"]:
            return "Teslim güncel iş çalışmasına ait değil."
        from .delivery_evidence import inspect
        view = inspect(item)
        if not view["complete"]:
            return "Kanıt eksik; teslim kabul edilemez: " + ", ".join(view["missing"])
        return ""

    @classmethod
    def _accept_metadata(cls, item: dict, work: dict, latest: int) -> dict:
        reason = cls._accept_reason(item, work, latest)
        return {**item, "is_latest": item["version"] == latest,
                "can_accept": not reason and item["status"] != "accepted",
                "accept_block_reason": reason}

    async def latest_deliverable(self, kind: str = "", bridge_id: str = "") -> dict | None:
        """Most recent delivery, optionally filtered by kind or bridge."""
        async with self.lock:
            if bridge_id:
                cursor = await self.db.execute(
                    "SELECT * FROM deliverables WHERE bridge_id=? "
                    "ORDER BY created_at DESC LIMIT 1", (bridge_id,))
            elif kind:
                cursor = await self.db.execute(
                    "SELECT * FROM deliverables WHERE kind=? ORDER BY created_at DESC LIMIT 1",
                    (kind,))
            else:
                cursor = await self.db.execute(
                    "SELECT * FROM deliverables ORDER BY created_at DESC LIMIT 1")
            row = await cursor.fetchone()
        return await self.get_deliverable(row[0]) if row else None

    async def latest_for_bridge(self, bridge_id: str) -> dict | None:
        """Latest delivery of a pack, matching either bridge_id or the kind prefix.

        Older office deliveries stored no ``bridge_id``; their ``kind`` is
        ``office-report``. Matching both keeps pack cards stable without
        rewriting history.
        """
        if not bridge_id:
            return None
        async with self.lock:
            async with self.db.execute(
                    "SELECT * FROM deliverables WHERE bridge_id=? OR kind LIKE ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (bridge_id, f"{bridge_id}-%")) as cur:
                row = await cur.fetchone()
        return await self.get_deliverable(row[0]) if row else None

    async def pending_repair(self, work_id: str) -> dict | None:
        """Latest deliverable still waiting for a repair run, if any."""
        deliverables = await self.list_deliverables(work_id)
        for deliverable in reversed(deliverables):
            if deliverable["status"] == "repair_requested":
                return deliverable
        return None

    async def find_deliverable(self, work_id: str, idempotency_key: str) -> dict | None:
        if not idempotency_key:
            return None
        async with self.lock:
            async with self.db.execute(
                    "SELECT * FROM deliverables WHERE work_id=? AND idempotency_key=?",
                    (work_id, idempotency_key)) as cur:
                row = await cur.fetchone()
        return await self.get_deliverable(row[0]) if row else None

    async def get_deliverable(self, deliverable_id: str) -> dict | None:
        async with self.lock:
            async with self.db.execute("SELECT * FROM deliverables WHERE id=?",
                                       (deliverable_id,)) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        item = self._deliverable(row)
        work = await self.get(item["work_id"])
        async with self.lock:
            cursor = await self.db.execute("SELECT MAX(version) FROM deliverables WHERE work_id=?", (item["work_id"],))
            latest = (await cursor.fetchone())[0]
        return self._accept_metadata(item, work or {}, latest)

    async def set_deliverable_status(self, deliverable_id: str, status: str,
                                     note: str = "") -> dict:
        if status not in DELIVERABLE_STATUSES:
            raise ValueError("Geçersiz teslim durumu.")
        item = await self.get_deliverable(deliverable_id)
        if item is None:
            raise ValueError("Teslim bulunamadı.")
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.db.execute("SELECT * FROM deliverables WHERE id=?", (deliverable_id,))
                row = await cursor.fetchone()
                if row is None:
                    raise ValueError("Teslim bulunamadı.")
                item = self._deliverable(row)
                cursor = await self.db.execute("SELECT MAX(version) FROM deliverables WHERE work_id=?", (item["work_id"],))
                latest = (await cursor.fetchone())[0]
                cursor = await self.db.execute(f"SELECT {self._select()} FROM work_items WHERE id=?", (item["work_id"],))
                work = self._row(await cursor.fetchone())
                if item["version"] != latest:
                    raise ValueError("Yalnız en güncel teslimin durumu değiştirilebilir.")
                if status == "accepted":
                    reason = self._accept_reason(item, work, latest)
                    if reason:
                        raise ValueError(reason)
                elif status in {"delivered", "test_passed"}:
                    from .delivery_evidence import inspect
                    view = inspect(item)
                    if not view["complete"]:
                        raise ValueError("Kanıt eksik; teslim durumu değiştirilemez: " + ", ".join(view["missing"]))
                await self.db.execute(
                    "UPDATE deliverables SET status=?,note=?,updated_at=? WHERE id=?",
                    (status, str(note)[:2000], _now(), deliverable_id))
                if status == "accepted":
                    await self.db.execute("UPDATE work_items SET status='done',next_action='',updated_at=? WHERE id=?",
                                          (_now(), item["work_id"]))
                elif status == "repair_requested":
                    await self.db.execute("UPDATE work_items SET status='open',run_id='',next_action=?,updated_at=? WHERE id=?",
                                          (f"Teslim v{item['version']} için düzeltme isteği: {str(note)[:400]}", _now(), item["work_id"]))
                elif status == "test_pending":
                    await self.db.execute("UPDATE work_items SET status='waiting',next_action=?,updated_at=? WHERE id=?",
                                          ("Kullanıcı testi bekleniyor.", _now(), item["work_id"]))
                elif status == "test_failed":
                    await self.db.execute("UPDATE work_items SET status='active',next_action=?,updated_at=? WHERE id=?",
                                          ("Kullanıcı testi başarısız; düzeltme gerekiyor.", _now(), item["work_id"]))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
        updated = await self.get_deliverable(deliverable_id)
        return updated

    async def accept(self, deliverable_id: str, note: str = "") -> dict:
        return await self.set_deliverable_status(deliverable_id, "accepted", note)

    async def request_repair(self, deliverable_id: str, note: str) -> dict:
        note = str(note or "").strip()
        if not note:
            raise ValueError("Düzeltme isteği için açıklama gerekli.")
        current = await self.get_deliverable(deliverable_id)
        if current is None:
            raise ValueError("Teslim bulunamadı.")
        updated = await self.set_deliverable_status(deliverable_id, "repair_requested", note)
        updated["repair_from_version"] = current["version"]
        return updated

    async def record_test(self, deliverable_id: str, passed: bool, note: str = "") -> dict:
        current = await self.get_deliverable(deliverable_id)
        if current is None:
            raise ValueError("Teslim bulunamadı.")
        status = "test_passed" if passed else "test_failed"
        if passed:
            await self.set_deliverable_status(deliverable_id, status, note)
        else:
            await self.set_deliverable_status(deliverable_id, status, note)
        return await self.get_deliverable(deliverable_id)
