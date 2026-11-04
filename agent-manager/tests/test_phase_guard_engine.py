import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from manager.engine import Engine
from manager.program import PhaseAudit

def test_archive_cannot_bypass_invalid_review():
    async def scenario():
        engine=Engine.__new__(Engine)
        engine.synthesize_summary=AsyncMock()
        run={"prompt":"task", "program_index":0,"program_items":[{"title":"phase","goal":"work"}],"allow_writes":True,"plan":{"steps":[{"id":"s","persona":"developer","dependencies":[]}]},"results":{"s":{"status":"completed","independent_review":{"status":"unavailable"}}}}
        with pytest.raises(ValueError,match="doğrulanmadan kapatılamaz"):
            await engine.archive_and_advance(run,PhaseAudit(action="accept",reason="model says done"))
        engine.synthesize_summary.assert_not_awaited()
    asyncio.run(scenario())

def test_model_accept_is_converted_to_repair_on_missing_review():
    async def scenario():
        engine=Engine.__new__(Engine)
        engine._architect_json=AsyncMock(return_value={"model_id":"reviewer","output":'{"action":"accept","reason":"looks good"}'})
        engine.change=AsyncMock();engine.refresh_project_memory=AsyncMock();engine.store=SimpleNamespace(event=AsyncMock())
        run={"id":"r","prompt":"task","workspace":".","program_index":0,"program_items":[{"title":"phase","goal":"work"}],"allow_writes":True,"plan":{"steps":[{"id":"s","persona":"developer","dependencies":[]}]},"results":{"s":{"status":"completed","independent_review":{"status":"unavailable"}}}}
        assert (await engine.audit_phase(run)).action=="repair"
    asyncio.run(scenario())
