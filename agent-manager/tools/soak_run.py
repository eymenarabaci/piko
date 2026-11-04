"""Faz 9 soak düzeneği: sahte saatle 24 saatlik dayanıklılık simülasyonu.

Gerçek zaman beklenmez. Her simüle "saat" sonunda iş sayıları, rastgele
onay/ret turu, çök-kalk, kota tükenmesi, kilit çakışması ve kurtarılan run
sayısı ``work/soak/saat.json`` dosyasına yazılır. Koşu sonunda çarpıcı özet
``work/soak/SOAK_RAPOR.md`` olarak üretilir.

Bu betik yalnız mevcut manager API'lerini çağırır; manager paketlerinin içi
değiştirilmez. Ofis ve iletişim paketleri gerçek fixture verisiyle çalışır.

Kullanım:
    .venv\\Scripts\\python.exe tools\\soak_run.py --hours 24 --jobs 48 --seed 9
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from bridge_packs.communication.fixtures import FixtureServer
from manager.bridge_gate import BridgeGate
from manager.engine import Engine
from manager.metrics import MetricsStore
from manager.model_router import ModelRouter
from manager.notifications import NotificationManager
from manager.operations import OperationLedger
from manager.packs import communication, communication_flow, office, office_flow
from manager.packs import registry as packs
from manager.processes import run_command
from manager.recovery import apply_user_answer, collect_proposals
from manager.resource_locks import LockTimeout, ResourceLocks
from manager.store import Store
from manager.work_record import WorkRecordManager

SCHEMA = "piko-soak/v1"
JOB_KINDS = ("office", "communication_mail", "communication_meeting", "manual")
PENDING_STATUSES = {"open", "active", "waiting"}


# --------------------------------------------------------------------- clocks


@dataclass
class FakeClock:
    """Controllable clock: the whole soak run advances without real sleeping."""

    start: datetime
    elapsed: timedelta = field(default_factory=timedelta)

    def now(self) -> datetime:
        return self.start + self.elapsed

    def advance(self, *, hours: float = 1.0) -> datetime:
        self.elapsed += timedelta(hours=float(hours))
        return self.now()

    def iso(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat()


class QuietProviders:
    """No model is contacted during the soak run."""

    models = [{"id": "worker", "provider": "test", "execution_supported": True}]

    def available(self):
        return [{"id": "test", "available": True, "executable": "test-only"}]

    async def run(self, model_id, prompt, *, workspace, allow_writes, timeout,
                  on_event, effort=""):
        return {"output": "ok", "model_id": model_id, "status": "completed", "usage": {}}


# ---------------------------------------------------------------------- stack


@dataclass
class SoakStack:
    engine: Engine
    store: Store
    work: WorkRecordManager
    ledger: OperationLedger
    locks: ResourceLocks
    metrics: MetricsStore
    notifications: NotificationManager
    router: ModelRouter


@asynccontextmanager
async def build_stack(workdir: Path, workspace: Path):
    store = Store(workdir / "runs.sqlite")
    await store.open()
    work = WorkRecordManager(workdir / "work.sqlite")
    await work.open()
    notifications = NotificationManager(workdir / "notifications.sqlite")
    await notifications.open()
    ledger = OperationLedger(workdir / "operations.sqlite")
    await ledger.open()
    metrics = MetricsStore(workdir / "metrics.sqlite")
    await metrics.open()
    locks = ResourceLocks(workdir / "locks.sqlite")
    await locks.open()
    bridge_gate = BridgeGate(token_store=ledger)
    packs.register_shipped(bridge_gate.registry)
    router = ModelRouter([
        {"id": "soak-codex", "provider": "codex", "base_model": "soak",
         "execution_supported": True, "capabilities": ["general"]},
        {"id": "soak-gemini", "provider": "antigravity", "base_model": "gemini-soak",
         "execution_supported": True, "capabilities": ["general"]},
    ], workdir / "token_ledger.sqlite")
    try:
        async with AsyncSqliteSaver.from_conn_string(str(workdir / "checkpoints.sqlite")) as saver:
            engine = Engine(store, saver, QuietProviders(), workspace)
            engine.attach_infrastructure(
                work=work, notifications=notifications, ledger=ledger,
                metrics=metrics, resource_locks=locks, bridge_gate=bridge_gate)
            try:
                yield SoakStack(engine, store, work, ledger, locks, metrics,
                                notifications, router)
            finally:
                await engine.close()
    finally:
        await locks.close()
        await metrics.close()
        await ledger.close()
        await notifications.close()
        await work.close()
        await store.close()


# -------------------------------------------------------------------- helpers


def _prepare_workdir(workdir: Path) -> None:
    """Her koşu sıfırdan başlasın: yalnız soak'a ait bilinen artefaktları sil."""
    databases = ("runs.sqlite", "work.sqlite", "notifications.sqlite",
                 "operations.sqlite", "metrics.sqlite", "locks.sqlite",
                 "checkpoints.sqlite", "token_ledger.sqlite", "communication.sqlite")
    for name in databases:
        for suffix in ("", "-wal", "-shm"):
            (workdir / f"{name}{suffix}").unlink(missing_ok=True)
    for path in workdir.glob("saat-*.json"):
        path.unlink(missing_ok=True)
    for directory in ("workspaces", "engine-workspace", "redline-workspace", "work"):
        shutil.rmtree(workdir / directory, ignore_errors=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, payload: dict) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _counts(items: list[dict]) -> dict:
    counts = {"total": len(items), "done": 0, "pending": 0, "blocked": 0,
              "cancelled": 0, "active": 0, "waiting": 0, "open": 0}
    for item in items:
        status = str(item.get("status") or "")
        if status == "done":
            counts["done"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        elif status == "cancelled":
            counts["cancelled"] += 1
        elif status in PENDING_STATUSES:
            counts["pending"] += 1
            counts[status] += 1
    return counts


async def _seed_communication(path: Path, clock: FakeClock) -> None:
    """Fixture verisi: gelen kutusu ve takvim; hiçbir canlı API'ye gidilmez."""
    server = await FixtureServer(path).open()
    today = clock.now().replace(microsecond=0)
    await server.seed_message(sender="ayse@example.test", subject="Sprint planı",
                              body="Perşembe 14:00 uygun mu?",
                              received_at=(today - timedelta(hours=2)).isoformat())
    await server.seed_message(sender="mehmet@example.test", subject="Fatura onayı",
                              body="Ekli faturayı onaylar mısınız?",
                              received_at=(today - timedelta(hours=1)).isoformat(),
                              attachments=[{"name": "fatura.pdf", "bytes": 2048}])
    await server.seed_message(sender="zeynep@example.test", subject="Toplantı notları",
                              body="Notlar ektedir.", unread=False,
                              received_at=(today - timedelta(minutes=30)).isoformat())
    await server.seed_event(title="Günlük standup",
                            start_at=today.replace(hour=9).isoformat(),
                            end_at=today.replace(hour=10).isoformat(),
                            attendees=["ekip@example.test"])
    await server.seed_event(title="Müşteri görüşmesi",
                            start_at=today.replace(hour=15).isoformat(),
                            end_at=today.replace(hour=16).isoformat(),
                            attendees=["musteri@example.test"])


async def _run_child(workdir: Path, code: str) -> dict:
    """Gerçek bir alt süreç çalıştır; çöküş/kalkış simülasyonunun kanıtı."""
    async def _noop(*_args, **_kwargs):
        return None

    try:
        result = await run_command([sys.executable, "-c", code], root=workdir,
                                   workspace=workdir, timeout=20, on_event=_noop)
        return {"returncode": result.get("returncode")}
    except Exception as exc:  # ProcessError, timeout, ortam kısıtı
        return {"returncode": None, "error": f"{type(exc).__name__}: {exc}"[:300]}


async def _process_fault_cycle(stack: SoakStack, workdir: Path, hour: int,
                               rng: random.Random, faults: dict) -> int:
    """Bir pending dış işlemi çökert, süreci kaldır, kurtarma sorusunu yanıtla."""
    operation = await stack.ledger.begin(
        "soak.process.crash", f"soak-crash-{hour}", {"hour": hour})
    crash = await _run_child(workdir, "import sys; sys.exit(7)" if hour % 2 else "raise SystemExit(9)")
    faults["process_runs"] += 1
    faults["crash_returncodes"].append(crash.get("returncode"))
    faults["crashes"] += 1

    # Süreç yeniden açılışı: pending işlem 'unknown' olur, otomatik tekrar yok.
    await stack.ledger.close()
    await stack.ledger.open()

    recovered = 0
    for proposal in await collect_proposals(stack.ledger):
        if proposal.get("operation_id") != operation["id"]:
            continue
        answer = "applied" if rng.random() < 0.5 else "not_applied"
        resolved = await apply_user_answer(
            stack.ledger, operation["id"], answer, note="soak çök-kalk kurtarması")
        recovered += 1 if resolved.get("changed") else 0
    faults["recovered_operations"] += recovered

    restart = await _run_child(
        workdir, "print('soak-restart-ok', flush=True)")
    faults["process_runs"] += 1
    faults["restart_returncodes"].append(restart.get("returncode"))
    if restart.get("returncode") == 0:
        faults["restarts"] += 1
    return recovered


async def _probe_red_lines(stack: SoakStack, workdir: Path) -> dict:
    """Faz 9 kırmızı çizgilerinin kısa kanıtı; hiçbir yazma gerçekleşmez."""
    result = {"unapproved_apply_refused": False, "proactive_draft_refused": False,
              "dangerous_level_refused": False}
    workspace = workdir / "redline-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    request = {"title": "Kırmızı çizgi kanıtı",
               "sections": [{"heading": "Özet", "body": "Yazılmamalı."}],
               "formats": ["md"]}
    try:
        await office_flow.deliver(
            stack.engine, persona=office.MANAGER_PERSONA, request=request,
            target="redline", idempotency_key="soak-redline-unapproved",
            workspace=str(workspace))
    except PermissionError:
        result["unapproved_apply_refused"] = True
    except Exception:
        pass
    try:
        await stack.engine.authorize_bridge(
            communication.PROACTIVE_PERSONA, communication.BRIDGE_ID, "draft",
            payload={}, proactive=True)
    except PermissionError:
        result["proactive_draft_refused"] = True
    except Exception:
        pass
    try:
        stack.engine.bridge_gate.registry.get(communication.BRIDGE_ID)
        await stack.engine.authorize_bridge(
            communication.APPROVER_PERSONA, communication.BRIDGE_ID, "dangerous",
            approval_token="soak-danger", idempotency_key="soak-danger")
    except PermissionError:
        result["dangerous_level_refused"] = True
    except Exception:
        pass
    return result


def _office_request(index: int) -> dict:
    return {
        "title": f"Soak haftalık raporu {index}",
        "sections": [
            {"heading": "Özet", "body": "Sprint hedefleri tamamlandı.",
             "bullets": ["Testler yeşil", "Teslim onaylandı"]},
            {"heading": "Riskler", "body": "Tek risk: model kotası."},
        ],
        "table_headers": ["Görev", "Durum", "Süre"],
        "rows": [["Planlama", "bitti", "2 sa"], ["Uygulama", "bitti", "5 sa"]],
        "formats": ["md", "csv"],
    }


def _mail_request(index: int) -> dict:
    return {"kind": "mail", "to": [f"ekip{index % 5}@example.test"],
            "subject": f"Soak durum maili {index}",
            "body": "Bu ileti soak simülasyonunda fixture outbox'a yazılır."}


def _meeting_request(index: int, clock: FakeClock) -> dict:
    start = clock.now() + timedelta(days=1)
    end = start + timedelta(hours=1)
    return {"kind": "meeting", "title": f"Soak planlama {index}",
            "start_at": start.isoformat(), "end_at": end.isoformat(),
            "attendees": [f"ekip{index % 5}@example.test"],
            "agenda": "Soak gündemi"}


async def _process_job(stack: SoakStack, row: dict, rng: random.Random, *,
                       clock: FakeClock, fixture_db: Path, approval_rate: float,
                       accept_rate: float, round_counts: dict) -> None:
    work_id = row["id"]
    kind = row["kind"]
    workspace = row["workspace"]
    index = row["index"]

    if kind == "manual":
        roll = rng.random()
        if roll < 0.5:
            await stack.work.update(work_id, status="done", next_action="")
        elif roll < 0.8:
            await stack.work.update(work_id, status="blocked",
                                    next_action="Soak: bağımlılık bekliyor.")
        else:
            await stack.work.add_decision(work_id, "Soak kararı bekleniyor.",
                                          options=["devam", "bekle"])
        return

    if rng.random() >= approval_rate:
        round_counts["rejected"] += 1
        await stack.work.add_decision(
            work_id, "Soak: onay reddedildi; revizyon gerekli.",
            options=["revize", "iptal"])
        await stack.work.update(work_id, status="blocked",
                                next_action="Onay reddedildi (soak).")
        return

    round_counts["approved"] += 1
    if kind == "office":
        request = _office_request(index)
        approval = await office_flow.issue_approval(office_flow.draft_payload(request), gate=stack.engine.bridge_gate, target="raporlar", workspace=workspace)
        result = await office_flow.deliver(
            stack.engine, persona=office.MANAGER_PERSONA, request=request,
            target="raporlar", approval_token=approval["approval_token"],
            idempotency_key=f"soak-office-{work_id}", work_id=work_id,
            run_id=f"soak-run-{work_id}", workspace=workspace)
    elif kind == "communication_mail":
        request = _mail_request(index)
        approval = await communication_flow.issue_approval(request, gate=stack.engine.bridge_gate, workspace=workspace, fixture_db=str(fixture_db))
        result = await communication_flow.deliver(
            stack.engine, persona=communication.MANAGER_PERSONA, request=request,
            approval_token=approval["approval_token"],
            idempotency_key=f"soak-mail-{work_id}", work_id=work_id,
            run_id=f"soak-run-{work_id}", workspace=workspace,
            fixture_db=str(fixture_db))
    else:
        request = _meeting_request(index, clock)
        approval = await communication_flow.issue_approval(request, gate=stack.engine.bridge_gate, workspace=workspace, fixture_db=str(fixture_db))
        result = await communication_flow.deliver(
            stack.engine, persona=communication.MANAGER_PERSONA, request=request,
            approval_token=approval["approval_token"],
            idempotency_key=f"soak-meeting-{work_id}", work_id=work_id,
            run_id=f"soak-run-{work_id}", workspace=workspace,
            fixture_db=str(fixture_db))

    if not result.get("ok"):
        error = result.get("error") or {}
        await stack.work.update(work_id, status="blocked",
                                next_action=str(error.get("message") or "Teslim başarısız."))
        return
    deliverable = result.get("deliverable")
    if not deliverable:
        await stack.work.update(work_id, status="waiting",
                                next_action="Teslim kaydı oluşmadı (soak).")
        return
    if rng.random() < accept_rate:
        await stack.work.accept(deliverable["id"], note="Soak: kullanıcı kabul etti.")
    else:
        await stack.work.request_repair(
            deliverable["id"], "Soak: kullanıcı revizyon istedi.")


def render_markdown(summary: dict) -> str:
    final = summary["final"]
    counts = final["jobs"]
    approvals = summary["approvals"]
    faults = summary["faults"]
    lines = [
        "# PikoSoak Raporu",
        "",
        f"- Simüle edilen süre: **{summary['hours']} saat**",
        f"- İş kaydı: **{summary['jobs']}** (tohum: `{summary['seed']}`)",
        f"- Gerçek geçen süre: **{summary['real_elapsed_seconds']} sn** "
        "(sahte saat; gerçek bekleme yok)",
        f"- Biten / bekleyen / blocked: **{counts['done']} / {counts['pending']} / "
        f"{counts['blocked']}**",
        f"- Onay turu: **{approvals['approved']} onay**, **{approvals['rejected']} ret**",
        f"- Kurtarılan run: **{summary['recovered_runs']}**",
        "",
        "## Saatlik akış",
        "",
        "| Saat | Biten | Bekleyen | Blocked | Onay | Ret | Kurtarılan | Çöküş | Kota | Kilit |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["hourly"]:
        job = row["jobs"]
        hour_approval = row["approvals"]["hour"]
        hour_faults = row["faults"]
        lines.append(
            f"| {row['simulated_hour']} | {job['done']} | {job['pending']} | "
            f"{job['blocked']} | {hour_approval['approved']} | "
            f"{hour_approval['rejected']} | {row['recovered_runs']} | "
            f"{hour_faults['crashes']} | {hour_faults['quota_exhausted']} | "
            f"{hour_faults['lock_conflicts']} |")
    lines += [
        "",
        "## Arıza enjeksiyonu",
        "",
        f"- Çök-kalk döngüsü: **{faults['crashes']}** çöküş, "
        f"**{faults['restarts']}** başarılı kalkış, "
        f"**{faults['process_runs']}** alt süreç.",
        f"- Kota: **{faults['quota_exhausted']}** tükenme, "
        f"**{faults['quota_recovered']}** toparlanma.",
        f"- Kilit: **{faults['lock_conflicts']}** çakışma, "
        f"**{faults['lock_recovered']}** devir.",
        f"- Kurtarılan işlem: **{faults['recovered_operations']}**.",
        "",
        "## Kırmızı çizgi kanıtları",
        "",
        f"- Onaysız `apply` reddedildi: **{summary['red_lines']['unapproved_apply_refused']}**",
        f"- Proaktif `draft` reddedildi: **{summary['red_lines']['proactive_draft_refused']}**",
        f"- Tanımsız `dangerous` reddedildi: **{summary['red_lines']['dangerous_level_refused']}**",
        "",
        "> Sahte saat nedeniyle 24 saat saniyeler içinde koşar; hiçbir canlı model, "
        "posta veya takvim API'sine gidilmez. Ofis yazımları ve iletişim gönderimleri "
        "yalnız geçici çalışma alanındaki fixture'lara dokunur.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- main run


async def run_soak(*, hours: int = 24, jobs: int = 48, seed: int = 9,
                   workdir: Path | str | None = None, approval_rate: float = 0.7,
                   accept_rate: float = 0.6, fault_every: int = 6,
                   progress=None) -> dict:
    hours = int(hours)
    jobs = int(jobs)
    if hours < 1:
        raise ValueError("hours en az 1 olmalı.")
    if jobs < hours:
        raise ValueError("her saate en az bir iş düşmeli: jobs >= hours.")
    if not 0 <= approval_rate <= 1 or not 0 <= accept_rate <= 1:
        raise ValueError("approval_rate ve accept_rate 0..1 arasında olmalı.")

    workdir = Path(workdir) if workdir is not None else ROOT / "work" / "soak"
    workdir.mkdir(parents=True, exist_ok=True)
    _prepare_workdir(workdir)
    workspace_root = workdir / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    engine_workspace = workdir / "engine-workspace"
    engine_workspace.mkdir(parents=True, exist_ok=True)
    fixture_db = workdir / "communication.sqlite"

    rng = random.Random(seed)
    clock = FakeClock(datetime.now(timezone.utc).replace(microsecond=0))
    started_real = time.monotonic()
    hourly: list[dict] = []
    approvals_total = {"approved": 0, "rejected": 0, "total": 0}
    faults = {"crashes": 0, "restarts": 0, "process_runs": 0,
              "crash_returncodes": [], "restart_returncodes": [],
              "quota_exhausted": 0, "quota_recovered": 0,
              "lock_conflicts": 0, "lock_recovered": 0,
              "recovered_operations": 0}
    recovered_runs = 0
    red_lines = {"unapproved_apply_refused": False, "proactive_draft_refused": False,
                 "dangerous_level_refused": False}
    quota_state = None
    lock_state = None

    async with build_stack(workdir, engine_workspace) as stack:
        await _seed_communication(fixture_db, clock)

        # Her saate düşen kovaya paket türlerini dağıt; tek işlik saatlerde
        # manual yerine mutlaka onay turu olan bir paket işi kalsın.
        buckets = [[] for _ in range(hours)]
        for index in range(jobs):
            buckets[index % hours].append(index)
        kinds_by_index = {}
        pack_kinds = [kind for kind in JOB_KINDS if kind != "manual"]
        cursor = 0
        for bucket in buckets:
            for index in bucket:
                if len(bucket) == 1:
                    kinds_by_index[index] = pack_kinds[cursor % len(pack_kinds)]
                else:
                    kinds_by_index[index] = JOB_KINDS[cursor % len(JOB_KINDS)]
                cursor += 1

        job_rows = []
        for index in range(jobs):
            kind = kinds_by_index[index]
            workspace = workspace_root / f"job-{index:03d}"
            workspace.mkdir(parents=True, exist_ok=True)
            priority = ("low", "normal", "high", "urgent")[index % 4]
            item = await stack.work.create(
                f"Soak işi {index}: {kind} paketini çalıştır ve teslimi tamamla.",
                title=f"Soak işi {index}",
                requirements=["Fixture verisiyle çalış", "Onay turundan geç"],
                acceptance=["Teslim kaydı var", "Kanıt üretildi"],
                priority=priority, workspace=str(workspace))
            job_rows.append({"id": item["id"], "index": index, "kind": kind,
                             "workspace": str(workspace)})

        for hour in range(1, hours + 1):
            # 1) Sahte saate göre süresi dolan kotaları ve kilitleri kurtar.
            if quota_state and clock.now() >= quota_state["until"]:
                cooldowns = await stack.router.get_cooldowns()
                if quota_state["model_id"] in cooldowns:
                    await stack.router.clear_cooldown(quota_state["model_id"])
                faults["quota_recovered"] += 1
                recovered_runs += 1
                quota_state = None
            if lock_state and clock.now() >= lock_state["until"]:
                await stack.locks.collect_stale(now=clock.now())
                try:
                    lease = await stack.locks.acquire(
                        lock_state["resource"], lock_state["run_id"], timeout=0, ttl=60)
                except LockTimeout:
                    faults["lock_conflicts"] += 1
                else:
                    await lease.release()
                    faults["lock_recovered"] += 1
                    recovered_runs += 1
                await lock_state["lease"].release()
                lock_state = None

            # 2) Kırmızı çizgi probu ve arıza enjeksiyonları.
            if hour == 1:
                red_lines = await _probe_red_lines(stack, workdir)
                await stack.router.record_failure(
                    "soak-codex", "quota exceeded; resets in 30m",
                    retry_after_seconds=1800)
                quota_state = {"model_id": "soak-codex",
                               "until": clock.now() + timedelta(minutes=30)}
                faults["quota_exhausted"] += 1

                resource = "workspace:soak-shared"
                lease = await stack.locks.acquire(
                    resource, "soak-run-a", timeout=0, ttl=60)
                try:
                    rival = await stack.locks.acquire(
                        resource, "soak-run-b", timeout=0, ttl=60)
                except LockTimeout:
                    faults["lock_conflicts"] += 1
                else:
                    await rival.release()
                lock_state = {"resource": resource, "run_id": "soak-run-b",
                              "lease": lease,
                              "until": clock.now() + timedelta(seconds=60)}

            if hour == 1 or (fault_every and hour % max(1, fault_every) == 0):
                recovered_runs += await _process_fault_cycle(
                    stack, workdir, hour, rng, faults)

            # 3) Bu saate düşen işleri paket akışlarından geçir.
            round_counts = {"approved": 0, "rejected": 0}
            residue = (hour - 1) % hours
            for row in job_rows:
                if row["index"] % hours != residue:
                    continue
                await _process_job(
                    stack, row, rng, clock=clock, fixture_db=fixture_db,
                    approval_rate=approval_rate, accept_rate=accept_rate,
                    round_counts=round_counts)

            # 4) Okuma seviyeleri her saat fixture üzerinde çalışsın.
            snapshot = await communication.proactive_read(override=str(fixture_db))
            if snapshot.get("inbox", {}).get("unread", 0) < 0:  # pragma: no cover
                raise RuntimeError("İletişim fixture'ı okunamadı.")

            approvals_total["approved"] += round_counts["approved"]
            approvals_total["rejected"] += round_counts["rejected"]
            approvals_total["total"] += round_counts["approved"] + round_counts["rejected"]

            # 5) Saati ilerlet ve saatlik raporu yaz.
            clock.advance(hours=1)
            items = await stack.work.list(limit=500)
            report = {
                "schema": SCHEMA,
                "simulated_hour": hour,
                "simulated_at": clock.iso(),
                "real_elapsed_seconds": round(time.monotonic() - started_real, 3),
                "jobs": _counts(items),
                "approvals": {"hour": dict(round_counts),
                              "cumulative": dict(approvals_total)},
                "recovered_runs": recovered_runs,
                "faults": dict(faults),
                "red_lines": dict(red_lines),
            }
            hourly.append(report)
            _write_json(workdir / "saat.json", report)
            _write_json(workdir / f"saat-{hour:02d}.json", report)
            if progress is not None:
                progress(hour, report)

    summary = {
        "schema": SCHEMA,
        "hours": hours,
        "jobs": jobs,
        "seed": seed,
        "real_elapsed_seconds": round(time.monotonic() - started_real, 3),
        "final": hourly[-1] if hourly else {},
        "hourly": hourly,
        "approvals": approvals_total,
        "faults": faults,
        "recovered_runs": recovered_runs,
        "red_lines": red_lines,
        "report_path": str(workdir / "saat.json"),
        "markdown_path": str(workdir / "SOAK_RAPOR.md"),
    }
    _write_json(workdir / "ozet.json", summary)
    _atomic_write(workdir / "SOAK_RAPOR.md", render_markdown(summary))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Piko Faz 9 soak simülasyonu")
    parser.add_argument("--hours", type=int, default=24, help="simüle saat sayısı")
    parser.add_argument("--jobs", type=int, default=48, help="iş kaydı sayısı")
    parser.add_argument("--seed", type=int, default=9, help="tekrarlanabilir rastgelelik")
    parser.add_argument("--workdir", default="", help="rapor ve SQLite dizini")
    parser.add_argument("--approval-rate", type=float, default=0.7)
    parser.add_argument("--accept-rate", type=float, default=0.6)
    parser.add_argument("--fault-every", type=int, default=6)
    args = parser.parse_args(argv)

    def progress(hour, report):
        job = report["jobs"]
        print(f"[soak] saat {hour:02d}: bitti={job['done']} "
              f"bekleyen={job['pending']} blocked={job['blocked']} "
              f"kurtarılan={report['recovered_runs']}")

    summary = asyncio.run(run_soak(
        hours=args.hours, jobs=args.jobs, seed=args.seed,
        workdir=args.workdir or None, approval_rate=args.approval_rate,
        accept_rate=args.accept_rate, fault_every=args.fault_every,
        progress=progress))
    final = summary["final"]["jobs"]
    print(f"[soak] tamam: {summary['hours']} saat, {summary['real_elapsed_seconds']} sn, "
          f"bitti={final['done']} bekleyen={final['pending']} blocked={final['blocked']}, "
          f"kurtarılan={summary['recovered_runs']}")
    print(f"[soak] rapor: {summary['markdown_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
