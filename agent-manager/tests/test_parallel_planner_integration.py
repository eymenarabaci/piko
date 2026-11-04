import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from manager.engine import Engine

@pytest.mark.parametrize("enabled",[True,False])
async def test_planner_parallel_bundle_is_explicit_and_persisted(enabled):
    engine=Engine.__new__(Engine)
    engine.models={"evren":{"id":"evren","provider":"evren"}}
    engine.model_availability=AsyncMock(return_value={"evren":True})
    engine.router=SimpleNamespace(rank=AsyncMock(return_value=[]),get_cooldowns=AsyncMock(return_value={}),record_success=AsyncMock(),record_failure=AsyncMock())
    payload={"parallel_plans":[{"id":name,"depends_on":[],"plan":{"steps":[{"id":"one","title":name,"prompt":"work","model_id":"auto"}]}} for name in ["a","b"]]}
    engine.providers=SimpleNamespace(run=AsyncMock(return_value={"output":json.dumps(payload)}))
    engine.store=SimpleNamespace(event=AsyncMock());engine.change=AsyncMock();engine.record_result_usage=AsyncMock();engine.emitter=lambda *_:AsyncMock()
    run={"id":"r","execution_backend":"direct","workspace":".","timeout_seconds":30,"allow_parallel_plans":enabled}
    if not enabled:
        with pytest.raises(ValueError):await engine.generate_valid_plan(run,"task","evren")
        engine.change.assert_not_awaited()
    else:
        plan,_=await engine.generate_valid_plan(run,"task","evren")
        assert len(plan.steps)==2 and all(not s.dependencies for s in plan.steps)
        origins=engine.change.call_args.kwargs["parallel_plan_origins"]
        assert {v["plan_id"] for v in origins.values()}=={"a","b"}
        assert any(c.args[1]=="parallel_plans_compiled" for c in engine.store.event.call_args_list)
