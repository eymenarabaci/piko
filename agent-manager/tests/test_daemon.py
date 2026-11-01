from datetime import datetime, timedelta

from manager.daemon import DriverDaemon, dry_run, main
from manager.operations import OperationLedger
from manager.recovery import apply_user_answer, collect_proposals, summarize
from manager.work_record import WorkRecordManager


class FakeHooks:
    def __init__(self):
        self.resumed = []
        self.started = []
        self.notifications = []
        self._sequence = 0

    async def resume_work(self, work):
        self.resumed.append(dict(work))
        return work.get("run_id")

    async def start_work(self, work):
        self._sequence += 1
        run_id = f"run-{self._sequence}"
        self.started.append((work["id"], run_id))
        return run_id

    async def notify(self, kind, title, body="", *, work_id="", run_id="",
                     dedupe_key="", severity="normal"):
        self.notifications.append({
            "kind": kind, "title": title, "body": body, "work_id": work_id,
            "run_id": run_id, "dedupe_key": dedupe_key, "severity": severity,
        })


async def _open_work(tmp_path):
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    return work


async def _open_daemon(tmp_path, work, hooks, **kwargs):
    daemon = DriverDaemon(work, hooks, state_path=tmp_path / "daemon.sqlite", **kwargs)
    await daemon.open()
    return daemon


async def test_active_work_resumes_after_crash_and_restart(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Uzun işi sürdür", run_id="run-persist", status="active")

    first_hooks = FakeHooks()
    first = await _open_daemon(tmp_path, work, first_hooks)
    await first.tick()
    await first.close()

    second_hooks = FakeHooks()
    second = await _open_daemon(tmp_path, work, second_hooks)
    await second.tick()
    await second.close()

    loaded = await work.get(item["id"])
    await work.close()

    assert [call["run_id"] for call in first_hooks.resumed] == ["run-persist"]
    assert [call["run_id"] for call in second_hooks.resumed] == ["run-persist"]
    assert loaded["status"] == "active"
    assert loaded["run_id"] == "run-persist"


async def test_due_open_work_starts_exactly_once(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Vadesi gelen iş", due_at="2020-01-01T00:00:00+00:00")
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick()
    await daemon.tick()

    loaded = await work.get(item["id"])
    await daemon.close()
    await work.close()

    assert len(hooks.started) == 1
    assert hooks.started[0][0] == item["id"]
    assert loaded["status"] == "active"
    assert loaded["run_id"] == hooks.started[0][1]


async def test_future_open_work_waits_for_due_time(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Gelecekteki iş", due_at="2030-01-01T00:00:00+00:00")
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick()
    before = await work.get(item["id"])
    await daemon.tick(now=datetime.fromisoformat("2030-01-01T00:00:01+00:00"))

    loaded = await work.get(item["id"])
    await daemon.close()
    await work.close()

    assert hooks.started[0][0] == item["id"]
    assert len(hooks.started) == 1
    assert before["status"] == "open"
    assert loaded["status"] == "active"


async def test_waiting_work_does_not_block_ready_work(tmp_path):
    work = await _open_work(tmp_path)
    waiting = await work.create("Karar bekleyen iş", run_id="waiting-run", status="waiting")
    await work.add_decision(waiting["id"], "Hangi renk?")
    ready = await work.create("Hazır iş", due_at="2020-01-01T00:00:00+00:00")
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick()

    waiting_after = await work.get(waiting["id"])
    ready_after = await work.get(ready["id"])
    await daemon.close()
    await work.close()

    assert [call[0] for call in hooks.started] == [ready["id"]]
    assert [call["run_id"] for call in hooks.resumed] == []
    assert waiting_after["status"] == "waiting"
    assert ready_after["status"] == "active"


async def test_blocked_work_is_skipped(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Bloke iş", status="blocked")
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick()

    loaded = await work.get(item["id"])
    await daemon.close()
    await work.close()

    assert hooks.started == []
    assert hooks.resumed == []
    assert loaded["status"] == "blocked"


async def test_blocked_work_returns_when_dependencies_are_done(tmp_path):
    work = await _open_work(tmp_path)
    dependency = await work.create("Tamamlanan bağımlılık", status="done")
    item = await work.create("Bağımlı iş", status="blocked",
                             dependencies=[dependency["id"]])
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick()

    loaded = await work.get(item["id"])
    await daemon.close()
    await work.close()

    assert [call[0] for call in hooks.started] == [item["id"]]
    assert loaded["status"] == "active"


async def test_decision_reminders_use_increasing_intervals(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Plan onayı")
    decision = await work.add_decision(item["id"], "Hangi yaklaşım?")
    created = datetime.fromisoformat(decision["created_at"])
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick(now=created + timedelta(seconds=2))
    await daemon.tick(now=created + timedelta(seconds=6))
    await daemon.tick(now=created + timedelta(seconds=30))

    await daemon.close()
    await work.close()

    indexes = [note["dedupe_key"].rsplit(":", 1)[1] for note in hooks.notifications]
    assert indexes == ["0", "1", "2"]
    assert all(note["kind"] == "decision" for note in hooks.notifications)


async def test_repeated_tick_does_not_repeat_same_reminder(tmp_path):
    work = await _open_work(tmp_path)
    item = await work.create("Tekrar kontrolü")
    decision = await work.add_decision(item["id"], "Devam edilsin mi?")
    created = datetime.fromisoformat(decision["created_at"])
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    moment = created + timedelta(seconds=2)
    await daemon.tick(now=moment)
    await daemon.tick(now=moment)

    await daemon.close()
    await work.close()

    assert len(hooks.notifications) == 1


async def test_pending_decision_blocks_work_after_72_seconds(tmp_path):
    work = await _open_work(tmp_path)
    dependency = await work.create("Tamamlanan bağımlılık", status="done")
    item = await work.create("Süresi dolan karar", dependencies=[dependency["id"]])
    decision = await work.add_decision(item["id"], "Onaylıyor musunuz?")
    created = datetime.fromisoformat(decision["created_at"])
    hooks = FakeHooks()
    daemon = await _open_daemon(tmp_path, work, hooks)

    await daemon.tick(now=created + timedelta(seconds=100))
    await daemon.tick(now=created + timedelta(seconds=101))

    loaded = await work.get(item["id"])
    await daemon.close()
    await work.close()

    assert loaded["status"] == "blocked"
    assert "bloke" in loaded["next_action"].lower()
    blocker_notes = [note for note in hooks.notifications if note["kind"] == "blocker"]
    assert len(blocker_notes) == 1


async def test_recovery_produces_one_question_per_unknown_without_changes(tmp_path):
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    pending = await ledger.begin("github", "pr-42", {"repo": "demo"})
    uncertain = await ledger.fail(pending["id"], "Kapanış sırasında yanıt yok.",
                                  uncertain=True)
    applied_operation = await ledger.begin("local_files", "write-1", {"path": "a.txt"})
    await ledger.complete(applied_operation["id"], {"written": True})

    proposals = await collect_proposals(ledger)
    repeated = await collect_proposals(ledger)
    stored = await ledger.get(pending["id"])
    await ledger.close()

    assert uncertain["status"] == "unknown"
    assert len(proposals) == 1
    assert len(repeated) == 1
    assert proposals[0]["operation_id"] == pending["id"]
    assert proposals[0]["question"].count("?") == 1
    assert proposals[0]["recommended"] == "manual_check"
    assert summarize(proposals)["auto_repaired"] == 0
    assert stored["status"] == "unknown"


async def test_recovery_answer_is_explicit_and_never_automatic(tmp_path):
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    pending = await ledger.begin("email", "send-7", {"to": "a@example.com"})
    await ledger.fail(pending["id"], "Belirsiz.", uncertain=True)

    untouched = await apply_user_answer(ledger, pending["id"], "manual_check")
    after_manual = await ledger.get(pending["id"])
    resolved = await apply_user_answer(ledger, pending["id"], "applied")
    after_applied = await ledger.get(pending["id"])
    await ledger.close()

    assert untouched["changed"] is False
    assert after_manual["status"] == "unknown"
    assert resolved["changed"] is True
    assert after_applied["status"] == "applied"


async def test_dry_run_reports_state_without_writing(tmp_path):
    work = await _open_work(tmp_path)
    await work.create("Rapor işi", due_at="2020-01-01T00:00:00+00:00")
    await work.close()
    ledger = OperationLedger(tmp_path / "operations.sqlite")
    await ledger.open()
    pending = await ledger.begin("drive", "upload-1", {"file": "x"})
    await ledger.fail(pending["id"], "Belirsiz.", uncertain=True)
    await ledger.close()

    report = dry_run(tmp_path)
    assert report["work_status"]["open"] == 1
    assert report["due_open"] == 1
    assert report["waiting_decisions"] == 0
    assert len(report["unknown_operations"]) == 1
    assert report["unknown_operations"][0]["operation_id"] == pending["id"]
    assert report["auto_actions"] == 0
    assert report["writes"] == 0

    reopened = OperationLedger(tmp_path / "operations.sqlite")
    await reopened.open()
    stored = await reopened.get(pending["id"])
    await reopened.close()
    assert stored["status"] == "unknown"

    assert main(["--dry-run", "--data-dir", str(tmp_path)]) == 0
