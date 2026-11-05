import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from manager.engine import Engine


def test_phase_audit_skips_hidden_preferred_and_hidden_ranked_models():
    async def scenario():
        e=Engine.__new__(Engine)
        e.models={"hidden":{"provider":"codex","hidden":True},"visible":{"provider":"opencode","hidden":False}}
        e.model_availability=AsyncMock(return_value={"hidden":True,"visible":True})
        e.router=SimpleNamespace(rank=AsyncMock(return_value=[SimpleNamespace(model_id="hidden"),SimpleNamespace(model_id="visible")]),get_cooldowns=AsyncMock(return_value={}),record_success=AsyncMock(),record_failure=AsyncMock())
        e.optimizer=SimpleNamespace(estimate_tokens=lambda _:10)
        e.providers=SimpleNamespace(run=AsyncMock(return_value={"output":'{"action":"accept","reason":"checks done"}'}))
        e.record_result_usage=AsyncMock();e.emitter=lambda *_:AsyncMock()
        await e._architect_json({"id":"r","planner_model":"hidden","execution_backend":"direct","workspace":".","timeout_seconds":30},"audit","phase_audit:0:0")
        assert e.providers.run.await_args.args[0]=="visible"
        assert e.providers.run.await_count==1
    asyncio.run(scenario())


def test_hidden_local_reader_is_never_a_fallback():
    e=Engine.__new__(Engine)
    e.models={"hidden":{"provider":"ollama","hidden":True},"visible":{"provider":"ollama","hidden":False}}
    assert e._local_reader_models({"hidden":True,"visible":True})==["visible"]
