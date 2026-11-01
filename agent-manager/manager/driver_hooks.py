"""Engine adapter for the unattended driver's DriverHooks protocol.

The daemon owns *when* work starts; the engine owns *how*. This adapter is the
only bridge between them and keeps both calls idempotent per work id:

* ``resume_work`` re-attaches an ``active`` work item to its existing run; it
  never creates a second run and never restarts a run that is already executing.
* ``start_work`` creates exactly one run for a due ``open`` item. The run id is
  recorded on the work row inside ``Engine.create`` (``create_for_run``), so a
  crash between create and link still resolves to a single run next tick.
* ``notify`` forwards to the shared NotificationManager, which de-duplicates.
* ``on_recovery`` surfaces unknown external operations as questions; it never
  repairs them.
"""
from __future__ import annotations

import logging

from .schema import RunRequest, workspace_path

log = logging.getLogger("manager.driver_hooks")

LIVE_STATUSES = {"queued", "planning", "running", "waiting_quota", "awaiting_review",
                 "awaiting_user_test", "planned"}
RESUMABLE_STATUSES = {"interrupted", "failed"}


class EngineDriverHooks:
    """DriverHooks implementation backed by a single ``Engine`` instance."""

    def __init__(self, engine, *, default_mode: str = "orchestrate",
                 allow_writes: bool = False, executor_model: str = "auto"):
        self.engine = engine
        self.default_mode = default_mode
        self.allow_writes = bool(allow_writes)
        self.executor_model = executor_model
        self.recovery_questions: list[dict] = []

    async def resume_work(self, work: dict) -> str | None:
        run_id = str(work.get("run_id") or "").strip()
        if not run_id:
            return None
        run = await self.engine.store.get(run_id)
        if run is None:
            # The run row is gone; re-open the work so the next tick can start it.
            log.warning("Aktif işin run kaydı yok, iş yeniden açılıyor: %s", work.get("id"))
            try:
                await self.engine.work.update(
                    work["id"], status="open", run_id="",
                    next_action="Run kaydı bulunamadı; iş yeniden başlatılacak.")
            except Exception:
                log.exception("İş yeniden açılamadı: %s", work.get("id"))
            return None
        task = self.engine.tasks.get(run_id)
        if task is not None and not task.done():
            # Already executing in this process; nothing to do.
            return run_id
        if run.get("status") in LIVE_STATUSES:
            return run_id
        if run.get("status") not in RESUMABLE_STATUSES:
            # Completed or cancelled runs are never restarted by the driver.
            return run_id
        try:
            await self.engine.resume(run_id)
        except Exception:
            log.exception("Run devam ettirilemedi: %s", run_id)
            return None
        return run_id

    async def start_work(self, work: dict) -> str | None:
        existing = str(work.get("run_id") or "").strip()
        if existing:
            return existing
        goal = str(work.get("goal") or "").strip()
        if not goal:
            return None
        return await self._create_run(work, goal)

    async def start_repair(self, work: dict, deliverable: dict) -> str | None:
        """Start the repair run for a ``repair_requested`` delivery.

        The new delivery is linked to the rejected version (parent_version+1);
        the user's note becomes part of the run prompt, and the work stays
        queued while the user reviews the result, so other work keeps moving.
        """
        existing = str(work.get("run_id") or "").strip()
        if existing:
            return existing
        base = str(work.get("goal") or "").strip()
        if not base:
            return None
        note = str((deliverable or {}).get("note") or "").strip()
        version = int((deliverable or {}).get("version") or 0)
        goal = base
        if note:
            goal = (f"{base}\n\nDüzeltme isteği (teslim v{version}): {note}\n"
                    "Önceki teslimi koru; yalnız istenen noktaları düzelt ve yeni "
                    "sürümü kanıtla.")
        else:
            goal = (f"{base}\n\nTeslim v{version} için düzeltme istendi; yeni "
                    "sürümü kanıtla.")
        run_id = await self._create_run(work, goal)
        if run_id and self.engine is not None and deliverable:
            try:
                await self.engine.record_repair_context(work["id"], deliverable, note)
            except Exception:
                log.exception("Onarım bağlamı kaydedilemedi: %s", work.get("id"))
        return run_id

    async def _create_run(self, work: dict, goal: str) -> str | None:
        workspace = ""
        target = work.get("workspace") or ""
        if target:
            try:
                workspace = workspace_path(str(target), self.engine.default_workspace)
            except ValueError:
                log.warning("Geçersiz çalışma alanı, varsayılan kullanılacak: %s", target)
        request = RunRequest(
            prompt=goal,
            mode=self.default_mode,
            workspace=workspace,
            allow_writes=self.allow_writes,
            planning_level="fast",
            planner_model="auto",
            executor_model=self.executor_model,
            work_id=str(work.get("id") or ""),
        )
        try:
            created = await self.engine.create(request)
        except Exception:
            log.exception("İş için run oluşturulamadı: %s", work.get("id"))
            return None
        run_id = created.get("id") if isinstance(created, dict) else None
        if not run_id:
            return None
        linked = await self.engine.work.by_run(run_id)
        if linked is None:
            # create() links the work row; fall back to an explicit link.
            try:
                await self.engine.work.update(
                    work["id"], run_id=run_id, status="active",
                    next_action="Görev başlatıldı.")
            except Exception:
                log.exception("İş kaydı run'a bağlanamadı: %s -> %s", work.get("id"), run_id)
        return run_id

    async def notify(self, kind: str, title: str, body: str = "", *,
                     work_id: str = "", run_id: str = "", dedupe_key: str = "",
                     severity: str = "normal") -> None:
        manager = self.engine.notifications
        if manager is None:
            return
        await manager.notify(kind, title, body, work_id=work_id, run_id=run_id,
                             dedupe_key=dedupe_key, severity=severity)

    async def beat(self, source_id: str, *, kind: str | None = None,
                   detail: str | None = None) -> None:
        """Forward the driver heartbeat to the health database."""
        from . import health
        await health.beat(source_id, kind=kind, detail=detail)

    async def on_recovery(self, proposals) -> None:
        """Store recovery questions; the UI asks exactly one question each."""
        self.recovery_questions = list(proposals or [])
        manager = self.engine.notifications
        for proposal in self.recovery_questions:
            if manager is None:
                break
            await manager.notify(
                "decision", "Dış işlem sonucu belirsiz",
                proposal.get("question", ""),
                dedupe_key=f"recovery:{proposal.get('operation_id')}",
                severity="high")

    async def answer_recovery(self, operation_id: str, answer: str, *, note: str = ""):
        """Apply an explicit user answer; the daemon never calls this itself."""
        from .recovery import apply_user_answer
        if self.engine.ledger is None:
            raise ValueError("İşlem defteri yapılandırılmadı.")
        result = await apply_user_answer(self.engine.ledger, operation_id, answer, note=note)
        self.recovery_questions = [item for item in self.recovery_questions
                                   if item.get("operation_id") != operation_id]
        return result
