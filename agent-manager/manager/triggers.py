"""Proactive scheduled & event-driven trigger daemon for autonomous agent workflows."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import uuid

from .schema import RunRequest

TEMPLATES = [
    {
        "template_id": "daily_health_check",
        "name": "Günlük Test & Sağlık Denetimi",
        "description": "Her 24 saatte bir projenin testlerini ve derleme sağlığını otonom kontrol eder.",
        "type": "interval",
        "interval_seconds": 86400,
        "prompt": "Projedeki mevcut birim ve entegrasyon testlerini çalıştır. Hata veya kırık test varsa özet rapor üret.",
        "mode": "orchestrate",
        "allow_writes": False,
    },
    {
        "template_id": "code_review_audit",
        "name": "Periyodik Kod ve Mimari Denetimi",
        "description": "Her 12 saatte bir projeyi mimari darboğazlar ve güvenlik açıkları açısından inceler.",
        "type": "interval",
        "interval_seconds": 43200,
        "prompt": "Proje kaynak kodunu ve mimari yapıyı incele; olası güvenlik risklerini ve performans darboğazlarını özetle.",
        "mode": "chat",
        "allow_writes": False,
    },
    {
        "template_id": "file_watcher",
        "name": "Dosya Değişikliği Doğrulayıcı (Watcher)",
        "description": "Proje dosyalarında değişiklik yapıldığında (30 sn sessizlik sonrası) otomatik doğrulama adımlarını çalıştırır.",
        "type": "file_watch",
        "interval_seconds": 30,
        "prompt": "Son değiştirilen dosyaları incele, derleme ve tip hatası olup olmadığını doğrula.",
        "mode": "chat",
        "allow_writes": False,
    },
    {
        "template_id": "memory_sync",
        "name": "Kalıcı Bellek & Mimari Senkronizasyonu",
        "description": "Her 6 saatte bir projenin güncel sembol ve bağımlılık haritasını kalıcı hafızaya işler.",
        "type": "interval",
        "interval_seconds": 21600,
        "prompt": "Proje dosya yapısını tara, yeni export edilen sembolleri ve mimari kararları kalıcı hafıza defterine kaydet.",
        "mode": "chat",
        "allow_writes": False,
    }
]

EXCLUDED_DIRS = {
    '.git', '.svn', '.venv', 'venv', 'env', 'node_modules', 'vendor',
    'dist', 'build', '__pycache__', '.pytest_cache', 'work', 'data'
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None


def _future_utc_run_at(value: object, now_dt: datetime | None = None) -> str:
    """Accept an explicit, future, timezone-aware ISO instant for one-shot work."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Tek seferlik görev için run_at gerekli.")
    try:
        run_dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("run_at ISO tarih/saat olmalı.") from exc
    if run_dt.tzinfo is None or run_dt.utcoffset() is None:
        raise ValueError("run_at saat dilimi içermeli.")
    run_utc = run_dt.astimezone(timezone.utc)
    if run_utc <= (now_dt or datetime.now(timezone.utc)):
        raise ValueError("run_at gelecekte olmalı; geçmiş görev sessizce başlatılmaz.")
    return run_utc.isoformat()


class TriggerManager:
    def __init__(self, data_dir: Path, engine=None):
        self.data_dir = Path(data_dir).resolve()
        self.file = self.data_dir / "triggers.json"
        self.engine = engine
        self._lock = threading.RLock()
        self._running = False
        self.update_pause = False
        self._daemon_task = None
        self._watch_state = {}  # workspace -> {"last_mtime": float, "change_detected_at": float | None}
        self._ensure_file()

    def _ensure_file(self):
        with self._lock:
            if not self.file.is_file():
                initial = {"version": 1, "triggers": []}
                self._save_atomic(initial)

    def _save_atomic(self, data: dict):
        temp = self.file.with_name(f"{self.file.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.file)
        finally:
            temp.unlink(missing_ok=True)

    def _load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "triggers": []}

    def list_triggers(self) -> list[dict]:
        with self._lock:
            return self._load().get("triggers", [])

    def list_templates(self) -> list[dict]:
        return list(TEMPLATES)

    def get_trigger(self, trigger_id: str) -> dict | None:
        with self._lock:
            for t in self._load().get("triggers", []):
                if t.get("id") == trigger_id:
                    return t
        return None

    def create_trigger(self, data: dict) -> dict:
        with self._lock:
            store = self._load()
            triggers = store.get("triggers", [])
            now_dt = datetime.now(timezone.utc)
            interval = max(10, int(data.get("interval_seconds", 3600)))
            t_type = data.get("type", "interval")
            if t_type not in {"interval", "file_watch", "once"}:
                t_type = "interval"

            next_run = None
            run_at = None
            if t_type == "interval":
                next_run = (now_dt + timedelta(seconds=interval)).isoformat()
            elif t_type == "once":
                run_at = _future_utc_run_at(data.get("run_at"), now_dt)
                next_run = run_at

            trigger = {
                "id": f"trg_{uuid.uuid4().hex[:12]}",
                "name": str(data.get("name", "Otonom Tetikleyici"))[:100],
                "description": str(data.get("description", ""))[:300],
                "type": t_type,
                "interval_seconds": interval,
                "run_at": run_at,
                "workspace": str(data.get("workspace", "")),
                "prompt": str(data.get("prompt", "Proje sağlık durumunu kontrol et."))[:4000],
                "mode": data.get("mode", "orchestrate") if data.get("mode") in {"chat", "plan", "orchestrate"} else "orchestrate",
                "allow_writes": bool(data.get("allow_writes", False)),
                "enabled": bool(data.get("enabled", True)),
                "template_id": data.get("template_id"),
                "created_at": _now(),
                "last_run_at": None,
                "next_run_at": next_run,
                "last_status": "idle",
                "last_run_id": None,
                "run_count": 0,
            }
            triggers.append(trigger)
            store["triggers"] = triggers
            self._save_atomic(store)
            return trigger

    def create_from_template(self, template_id: str, workspace: str = "") -> dict | None:
        tpl = next((t for t in TEMPLATES if t["template_id"] == template_id), None)
        if not tpl:
            return None
        payload = dict(tpl)
        if workspace:
            payload["workspace"] = workspace
        return self.create_trigger(payload)

    def update_trigger(self, trigger_id: str, data: dict) -> dict | None:
        with self._lock:
            store = self._load()
            triggers = store.get("triggers", [])
            target = None
            for t in triggers:
                if t.get("id") == trigger_id:
                    if t.get("type") == "once":
                        if "run_at" in data:
                            run_at = _future_utc_run_at(data["run_at"])
                            t["run_at"] = run_at
                            t["next_run_at"] = run_at
                            t["last_status"] = "idle"
                        if data.get("enabled") is True and (
                            t.get("run_count", 0) or not t.get("next_run_at")
                        ) and "run_at" not in data:
                            raise ValueError("Çalışmış tek seferlik görevi açmak için yeni run_at verin.")
                    for key in ["name", "description", "prompt", "workspace", "allow_writes"]:
                        if key in data:
                            t[key] = data[key]
                    if "mode" in data and data["mode"] in {"chat", "plan", "orchestrate"}:
                        t["mode"] = data["mode"]
                    if "enabled" in data:
                        t["enabled"] = bool(data["enabled"])
                    if "interval_seconds" in data:
                        t["interval_seconds"] = max(10, int(data["interval_seconds"]))
                        if t.get("type") == "interval":
                            t["next_run_at"] = (datetime.now(timezone.utc) + timedelta(seconds=t["interval_seconds"])).isoformat()
                    target = t
                    break
            if target:
                self._save_atomic(store)
            return target

    def delete_trigger(self, trigger_id: str) -> bool:
        with self._lock:
            store = self._load()
            triggers = store.get("triggers", [])
            new_triggers = [t for t in triggers if t.get("id") != trigger_id]
            if len(new_triggers) == len(triggers):
                return False
            store["triggers"] = new_triggers
            self._save_atomic(store)
            return True

    def record_run_start(self, trigger_id: str, run_id: str):
        with self._lock:
            store = self._load()
            for t in store.get("triggers", []):
                if t.get("id") == trigger_id:
                    t["last_run_at"] = _now()
                    t["last_run_id"] = run_id
                    t["last_status"] = "running"
                    t["run_count"] = t.get("run_count", 0) + 1
                    if t.get("type") == "interval":
                        sec = t.get("interval_seconds", 3600)
                        t["next_run_at"] = (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()
                    elif t.get("type") == "once":
                        t["enabled"] = False
                        t["next_run_at"] = None
                    break
            self._save_atomic(store)

    def record_run_finish(self, trigger_id: str, status: str):
        with self._lock:
            store = self._load()
            for t in store.get("triggers", []):
                if t.get("id") == trigger_id:
                    t["last_status"] = status
                    break
            self._save_atomic(store)

    def _scan_max_mtime(self, workspace: Path) -> float:
        if not workspace.is_dir():
            return 0.0
        max_m = 0.0
        try:
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith('.')]
                for f in files:
                    p = Path(root) / f
                    try:
                        m = p.stat().st_mtime
                        if m > max_m:
                            max_m = m
                    except OSError:
                        pass
        except OSError:
            pass
        return max_m

    async def execute_trigger(self, trigger: dict) -> str | None:
        if not self.engine:
            return None
        t_id = trigger.get("id")
        workspace = trigger.get("workspace") or getattr(self.engine, "default_workspace", str(Path.cwd()))
        prompt = f"[Proaktif Görev: {trigger.get('name')}]\n{trigger.get('prompt')}"
        req = RunRequest(
            prompt=prompt,
            mode=trigger.get("mode", "orchestrate"),
            workspace=workspace,
            allow_writes=trigger.get("allow_writes", False),
            planning_level="fast"
        )
        try:
            created = await self.engine.create(req)
            run_id = created.get("id") if isinstance(created, dict) else getattr(created, "id", None)
            if run_id:
                self.record_run_start(t_id, run_id)
                if hasattr(self.engine, "store") and hasattr(self.engine.store, "event"):
                    try:
                        await self.engine.store.event(
                            run_id,
                            "proactive_trigger_fired",
                            f"Proaktif tetikleyici '{trigger.get('name')}' otonom olarak devreye girdi.",
                            {"trigger_id": t_id, "type": trigger.get("type")}
                        )
                    except Exception:
                        pass
                return run_id
        except Exception as exc:
            with self._lock:
                store = self._load()
                for t in store.get("triggers", []):
                    if t.get("id") == t_id:
                        t["last_status"] = f"error: {str(exc)[:50]}"
                        if t.get("type") == "once":
                            t["enabled"] = False
                            t["next_run_at"] = None
                        break
                self._save_atomic(store)
        return None

    async def run_daemon(self):
        """Continuously inspect interval triggers and workspace file watchers."""
        self._running = True
        while self._running:
            if self.update_pause:
                await asyncio.sleep(1)
                continue
            try:
                now_dt = datetime.now(timezone.utc)
                now_ts = now_dt.timestamp()
                triggers = self.list_triggers()

                for t in triggers:
                    if not t.get("enabled", True):
                        continue

                    t_id = t.get("id")
                    t_type = t.get("type")

                    # 1. Interval Check
                    if t_type == "interval":
                        next_dt = _parse_iso(t.get("next_run_at"))
                        if next_dt is None or now_dt >= next_dt:
                            if t.get("last_status") != "running":
                                await self.execute_trigger(t)

                    # One-shot check: mark consumed at run start; never repeat.
                    elif t_type == "once":
                        next_dt = _parse_iso(t.get("next_run_at"))
                        if next_dt is not None and next_dt.tzinfo is not None and now_dt >= next_dt:
                            if t.get("last_status") != "running":
                                await self.execute_trigger(t)

                    # 2. File Watcher Check
                    elif t_type == "file_watch":
                        ws_str = t.get("workspace")
                        if not ws_str:
                            continue
                        ws_path = Path(ws_str).resolve()
                        if not ws_path.is_dir():
                            continue

                        debounce_sec = max(10, t.get("interval_seconds", 30))
                        ws_key = str(ws_path)
                        state = self._watch_state.setdefault(ws_key, {"last_mtime": 0.0, "change_detected_at": None})

                        current_max_mtime = await asyncio.to_thread(self._scan_max_mtime, ws_path)
                        if state["last_mtime"] == 0.0:
                            state["last_mtime"] = current_max_mtime
                        elif current_max_mtime > state["last_mtime"]:
                            # File modification detected, record timestamp
                            state["last_mtime"] = current_max_mtime
                            state["change_detected_at"] = now_ts
                        elif state["change_detected_at"] is not None:
                            # Quiet period elapsed?
                            if (now_ts - state["change_detected_at"]) >= debounce_sec:
                                state["change_detected_at"] = None
                                if t.get("last_status") != "running":
                                    await self.execute_trigger(t)

            except Exception:
                pass

            await asyncio.sleep(5)

    async def stop_daemon(self):
        self._running = False
