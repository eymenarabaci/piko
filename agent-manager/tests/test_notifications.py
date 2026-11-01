from datetime import datetime, timezone

from manager.notifications import NotificationManager


async def test_notification_dedupe_and_preferences(tmp_path):
    manager = NotificationManager(tmp_path / "notify.sqlite")
    await manager.open()
    first = await manager.notify("delivery", "Teslim hazır", dedupe_key="run1:delivery:1")
    second = await manager.notify("delivery", "Teslim hazır", dedupe_key="run1:delivery:1")
    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    assert len(await manager.list()) == 1

    await manager.set_preferences(enabled=False)
    suppressed = await manager.notify("blocker", "Engel var", dedupe_key="run2:blocker")
    assert suppressed["suppressed_reason"] == "disabled"
    visible = await manager.list()
    assert [item["id"] for item in visible] == [first["id"]]
    assert len(await manager.list(include_suppressed=True)) == 2
    await manager.close()


async def test_quiet_hours_defer_then_release(tmp_path):
    manager = NotificationManager(tmp_path / "notify.sqlite")
    await manager.open()
    await manager.set_preferences(quiet_start="22:00", quiet_end="08:00", timezone="Europe/Istanbul")
    night = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
    deferred = await manager.notify("decision", "Karar bekleniyor", dedupe_key="d1",
                                    moment=night)
    assert deferred["deferred"] is True
    assert deferred["delivered_at"] is None

    released = await manager.release_deferred(
        datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))
    assert [item["id"] for item in released] == [deferred["id"]]
    assert (await manager.list(unread_only=True))[0]["deferred"] is False

    read = await manager.mark_read(deferred["id"])
    assert read["read"] is True
    await manager.close()


async def test_severity_threshold(tmp_path):
    manager = NotificationManager(tmp_path / "notify.sqlite")
    await manager.open()
    await manager.set_preferences(min_severity="high", quiet_start="00:00", quiet_end="00:00")
    low = await manager.notify("info", "Bilgi", severity="low", dedupe_key="l1")
    high = await manager.notify("blocker", "Kritik engel", severity="high", dedupe_key="h1")
    assert low["suppressed_reason"] == "below_severity"
    assert high["delivered_at"]
    await manager.close()
