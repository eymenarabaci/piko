from datetime import datetime, timezone

import pytest

from manager.scheduling import (event_key, is_quiet_moment, missed_run, next_window,
                                ordered_work, proactive_decision)


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_order_work_uses_deadline_priority_and_dependencies():
    items = [
        {"id": "later", "priority": "urgent", "due_at": "2026-11-05T10:00:00+00:00",
         "dependencies": [], "created_at": "2026-11-01T08:00:00+00:00"},
        {"id": "overdue", "priority": "low", "due_at": "2026-10-30T10:00:00+00:00",
         "dependencies": [], "created_at": "2026-11-01T08:00:00+00:00"},
        {"id": "dependent", "priority": "high", "due_at": "2026-11-02T10:00:00+00:00",
         "dependencies": ["later"], "created_at": "2026-11-01T08:00:00+00:00"},
        {"id": "same_day_high", "priority": "high", "due_at": "2026-11-02T10:00:00+00:00",
         "dependencies": [], "created_at": "2026-11-01T07:00:00+00:00"},
        {"id": "same_day_normal", "priority": "normal", "due_at": "2026-11-02T10:00:00+00:00",
         "dependencies": [], "created_at": "2026-11-01T06:00:00+00:00"},
    ]
    ordered = ordered_work(items, now=NOW)
    assert [item["id"] for item in ordered] == [
        "overdue", "same_day_high", "same_day_normal", "later", "dependent"]
    assert ordered[0]["overdue"] is True


def test_dependency_cycle_is_rejected():
    items = [{"id": "a", "dependencies": ["b"]}, {"id": "b", "dependencies": ["a"]}]
    with pytest.raises(ValueError):
        ordered_work(items, now=NOW)


def test_quiet_hours_and_next_window():
    late = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
    assert is_quiet_moment(late, "22:00", "08:00") is True
    allowed = next_window(late, hour=9, quiet_start="22:00", quiet_end="08:00")
    assert allowed > late
    assert allowed.utcoffset() is not None


def test_missed_run_grace_and_stale():
    fresh = missed_run({"next_run_at": "2026-11-01T11:00:00+00:00"}, now=NOW)
    stale = missed_run({"next_run_at": "2026-10-20T11:00:00+00:00"}, now=NOW)
    assert fresh["should_run"] is True
    assert stale["should_run"] is False


def test_proactive_decision_dedupes_and_respects_budget():
    event = {"kind": "file_changed", "id": "abc", "version": "2"}
    rules = {"enabled": True, "quiet_start": "00:00", "quiet_end": "00:00",
             "daily_budget": 2, "used_today": 2}
    denied = proactive_decision(event, rules, now=NOW)
    assert denied["act"] is False
    assert "bütçe" in denied["reason"]
    rules["used_today"] = 0
    first = proactive_decision(event, rules, now=NOW)
    assert first["act"] is True
    second = proactive_decision({**event, "seen_key": event_key("file_changed", "abc", "2")},
                                rules, now=NOW)
    assert second["act"] is False
    assert "önce" in second["reason"]
