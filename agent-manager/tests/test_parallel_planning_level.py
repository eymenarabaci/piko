import pytest
from manager.schema import RunRequest
from manager.planning_policy import planning_guidance

@pytest.mark.parametrize("writes",[True,False])
def test_parallel_level_sets_safe_execution_options(writes):
    r=RunRequest(prompt="task",planning_level="parallel",allow_writes=writes,execution_backend="cao",max_parallel=6)
    assert r.allow_parallel_plans and r.execution_backend=="direct"
    assert r.parallel_writes is writes and r.allow_writes is writes

@pytest.mark.parametrize("level",["fast","balanced","detailed"])
def test_existing_levels_do_not_enable_parallel_options(level):
    r=RunRequest(prompt="task",planning_level=level)
    assert not r.parallel_writes and not r.allow_parallel_plans

def test_parallel_guidance_is_conditional():
    assert "zorunlu tutma" in planning_guidance("parallel")
