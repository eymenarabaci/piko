import json
import pytest
from manager.step_review import parse_review

def test_long_evidence_preserves_failure_and_every_finding():
    review=parse_review(json.dumps({"status":"fail","findings":[{"path":"engine.py","issue":"quota not verified","evidence":"proof "*300},{"path":"tests.py","issue":"missing assertion","evidence":"short"}],"test_result":"x"*1500}))
    assert review.status=="fail"
    assert len(review.findings)==2
    assert len(review.findings[0].evidence)==700
    assert review.findings[1].evidence=="short"

def test_malformed_failure_still_rejected():
    with pytest.raises(ValueError):parse_review('{"status":"fail","findings":[]}')


def test_unavailable_review_never_completes_implementation():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from manager.engine import Engine
    async def scenario():
        engine=Engine.__new__(Engine)
        engine.route=AsyncMock(side_effect=RuntimeError("review transport failed"))
        engine.store=SimpleNamespace(event=AsyncMock())
        with pytest.raises(RuntimeError,match="doğrulanmış sayılmadı"):
            await engine._verify_implementation_locked({"id":"r"},SimpleNamespace(model_id="m",title="step",id="s"),{"model_id":"m"})
    asyncio.run(scenario())


def test_user_test_requirement_cannot_be_marked_complete():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from manager.engine import Engine
    async def scenario():
        engine=Engine.__new__(Engine)
        engine.route=AsyncMock(return_value=SimpleNamespace(model_id="reviewer"))
        engine.router=SimpleNamespace(record_success=AsyncMock())
        engine.providers=SimpleNamespace(run=AsyncMock(return_value={"model_id":"reviewer","output":'{"status":"needs_user_test","question":"Confirm real output"}'}))
        engine.store=SimpleNamespace(event=AsyncMock())
        engine.record_result_usage=AsyncMock()
        step=SimpleNamespace(model_id="writer",title="step",id="s",prompt="do work")
        with pytest.raises(RuntimeError,match="kullanıcı doğrulaması gerekiyor"):
            await engine._verify_implementation_locked({"id":"r","prompt":"goal","timeout_seconds":0,"workspace":"."},step,{"model_id":"writer","output":"done"})
    asyncio.run(scenario())
