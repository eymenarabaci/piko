"""adim_3 driver regression: dependency ordering in the unattended driver.

Gap matrix I-02 controls:
* a dependent job whose dependency finished long ago -- outside the driver's
  500-row work window -- must still start (``done_ids`` is not window-bound);
* a cyclic or missing dependency is an explicit, recorded error (log +
  notification + persisted ``next_action``), never a silent skip that leaves
  the job open forever.
"""
from __future__ import annotations

from manager.daemon import DriverDaemon, WORK_LIMIT
from manager.work_record import WorkRecordManager


class FakeHooks:
    def __init__(self):
        self.started = []
        self.resumed = []
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


async def _open(tmp_path):
    work = WorkRecordManager(tmp_path / "work.sqlite")
    await work.open()
    hooks = FakeHooks()
    daemon = DriverDaemon(work, hooks, state_path=tmp_path / "daemon.sqlite")
    await daemon.open()
    return work, hooks, daemon


async def _close(work, daemon):
    await daemon.close()
    await work.close()


async def test_dependency_finished_before_the_window_still_starts(tmp_path):
    """A dependency done outside the newest-500 window is still "done"."""
    work, hooks, daemon = await _open(tmp_path)
    first = await work.create("Eski tamamlanmis bagimlilik", status="done")
    for index in range(WORK_LIMIT + 50):
        await work.create(f"Arsiv isi {index}", status="done")
    dependent = await work.create("Bagimli isi", dependencies=[first["id"]])

    await daemon.tick()

    loaded = await work.get(dependent["id"])
    await _close(work, daemon)

    assert [call[0] for call in hooks.started] == [dependent["id"]], \
        "pencere disinda kalan tamamlanmis bagimlilik 'done' sayilmali"
    assert loaded["status"] == "active"
    assert daemon.stats["skipped"] == 0


async def test_cyclic_dependencies_surface_an_explicit_error(tmp_path):
    work, hooks, daemon = await _open(tmp_path)
    one = await work.create("Dongu A")
    two = await work.create("Dongu B", dependencies=[one["id"]])
    await work.update(one["id"], dependencies=[two["id"]])

    await daemon.tick()

    flags = [note for note in hooks.notifications if note["kind"] == "dependency_error"]
    assert flags, "dongusel bagimlilik sessizce gecistirilemez"
    assert daemon.stats.get("dependency_errors", 0) >= 1
    assert hooks.started == [], "dongulu plan is yurutulmez"
    for item in (await work.get(one["id"]), await work.get(two["id"])):
        assert "hata" in item["next_action"].lower()

    # The error is flagged once per job, not once per tick.
    await daemon.tick()
    again = [note for note in hooks.notifications if note["kind"] == "dependency_error"]
    assert len(again) == len(flags)

    await _close(work, daemon)


async def test_missing_dependency_is_an_explicit_error(tmp_path):
    work, hooks, daemon = await _open(tmp_path)
    item = await work.create("Eksik bagimlilik", dependencies=["ghost-work-id"])

    await daemon.tick()

    flags = [note for note in hooks.notifications if note["kind"] == "dependency_error"]
    assert flags, "var olmayan bagimlilik acik hata olmali"
    assert [note["work_id"] for note in flags] == [item["id"]]
    assert hooks.started == []
    loaded = await work.get(item["id"])
    assert loaded["status"] == "open"
    assert "hata" in loaded["next_action"].lower()

    await _close(work, daemon)


async def test_waiting_on_active_dependency_is_not_an_error(tmp_path):
    """A job waiting on an active dependency is normal flow, not a defect."""
    work, hooks, daemon = await _open(tmp_path)
    parent = await work.create("Aktif bagimlilik", status="active", run_id="run-active")
    child = await work.create("Bekleyen bagimli", dependencies=[parent["id"]])

    await daemon.tick()

    assert [note for note in hooks.notifications if note["kind"] == "dependency_error"] == []
    assert daemon.stats.get("dependency_errors", 0) == 0
    assert hooks.started == []
    assert (await work.get(child["id"]))["status"] == "open"

    await _close(work, daemon)
