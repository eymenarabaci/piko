"""Quota timers resume persisted runs without contacting model providers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from manager.engine import Engine


class MemoryStore:
    def __init__(self, run):
        self.run = run
        self.events = []

    async def get(self, run_id):
        return dict(self.run)

    async def save(self, run):
        self.run = dict(run)

    async def event(self, run_id, kind, message, data=None):
        self.events.append(kind)


@pytest.mark.asyncio
async def test_expired_quota_timer_resumes_without_model_call():
    timer = {"step_id": "one", "model_id": "writer",
             "resets_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
             "execute": True}
    engine = Engine.__new__(Engine)
    engine.store = MemoryStore({"id": "run", "status": "waiting_quota", "quota_timer": timer})
    engine.locks = {}
    engine.quota_waiters = {}
    started = []
    engine.start = lambda run_id, execute=False: started.append((run_id, execute))
    engine.schedule_quota_resume("run", timer)
    await asyncio.sleep(0.01)
    assert engine.store.run["status"] == "queued"
    assert engine.store.run["quota_timer"] is None
    assert started == [("run", True)]
    assert "quota_resumed" in engine.store.events
