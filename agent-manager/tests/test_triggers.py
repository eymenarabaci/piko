"""Unit tests for TriggerManager and autonomous trigger logic."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from manager.triggers import TriggerManager, TEMPLATES


def test_trigger_templates_available(tmp_path):
    tm = TriggerManager(tmp_path)
    templates = tm.list_templates()
    assert len(templates) >= 3
    ids = {t["template_id"] for t in templates}
    assert "daily_health_check" in ids
    assert "file_watcher" in ids


def test_trigger_crud_and_persistence(tmp_path):
    tm = TriggerManager(tmp_path)
    created = tm.create_trigger({
        "name": "Test Tetikleyici",
        "description": "Açıklama",
        "type": "interval",
        "interval_seconds": 60,
        "prompt": "Test çalıştır",
        "workspace": str(tmp_path)
    })
    assert created["id"].startswith("trg_")
    assert created["enabled"] is True
    assert created["next_run_at"] is not None

    # List
    triggers = tm.list_triggers()
    assert len(triggers) == 1
    assert triggers[0]["name"] == "Test Tetikleyici"

    # Get
    single = tm.get_trigger(created["id"])
    assert single is not None
    assert single["name"] == "Test Tetikleyici"

    # Update
    updated = tm.update_trigger(created["id"], {"enabled": False, "name": "Güncel İsim"})
    assert updated["enabled"] is False
    assert updated["name"] == "Güncel İsim"

    # Template create
    from_tpl = tm.create_from_template("daily_health_check", workspace=str(tmp_path))
    assert from_tpl is not None
    assert len(tm.list_triggers()) == 2

    # Delete
    deleted = tm.delete_trigger(created["id"])
    assert deleted is True
    assert len(tm.list_triggers()) == 1


def complete_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected real IO in isolated test')


def test_trigger_execution_with_mock_engine(tmp_path):
    class MockEngine:
        def __init__(self):
            self.created_requests = []
            self.default_workspace = str(tmp_path)

        async def create(self, req):
            self.created_requests.append(req)
            return {"id": "mock_run_123"}

    engine = MockEngine()
    tm = TriggerManager(tmp_path, engine=engine)

    trigger = tm.create_trigger({
        "name": "Otonom Test",
        "prompt": "Testleri çalıştır",
        "interval_seconds": 10
    })

    run_id = complete_immediate(tm.execute_trigger(trigger))
    assert run_id == "mock_run_123"
    assert len(engine.created_requests) == 1
    assert "[Proaktif Görev: Otonom Test]" in engine.created_requests[0].prompt

    # Status updated
    updated_t = tm.get_trigger(trigger["id"])
    assert updated_t["last_run_id"] == "mock_run_123"
    assert updated_t["run_count"] == 1


def test_once_trigger_normalizes_future_time_and_disables_after_run(tmp_path):
    tm = TriggerManager(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    local_offset = timezone(timedelta(hours=3))
    created = tm.create_trigger({
        "type": "once", "run_at": future.astimezone(local_offset).isoformat(),
        "prompt": "Demoyu göster",
    })
    assert created["run_at"] == created["next_run_at"]
    assert datetime.fromisoformat(created["run_at"]).utcoffset() == timedelta(0)
    tm.record_run_start(created["id"], "scheduled-run")
    consumed = tm.get_trigger(created["id"])
    assert consumed["run_count"] == 1
    assert consumed["enabled"] is False
    assert consumed["next_run_at"] is None


@pytest.mark.parametrize("run_at", [
    None, "yarın", "2027-01-01T12:00:00", "2000-01-01T12:00:00+00:00",
])
def test_once_rejects_missing_naive_or_past_run_at(tmp_path, run_at):
    tm = TriggerManager(tmp_path)
    with pytest.raises(ValueError, match="run_at"):
        tm.create_trigger({"type": "once", "run_at": run_at})
    assert tm.list_triggers() == []


def test_once_rearm_requires_new_future_instant(tmp_path):
    tm = TriggerManager(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    created = tm.create_trigger({"type": "once", "run_at": future.isoformat()})
    tm.record_run_start(created["id"], "first")
    with pytest.raises(ValueError, match="yeni run_at"):
        tm.update_trigger(created["id"], {"enabled": True})
    rearmed = tm.update_trigger(created["id"], {
        "enabled": True, "run_at": (future + timedelta(hours=1)).isoformat(),
    })
    assert rearmed["enabled"] is True
    assert rearmed["next_run_at"] == rearmed["run_at"]
