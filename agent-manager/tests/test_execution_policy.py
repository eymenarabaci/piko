import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from manager import execution_policy as policy
from manager.providers import Providers, ProviderError
from manager.cao import CAOClient, CAOError
from manager.model_router import _backend_allows
from manager.engine import Engine

@pytest.fixture
def restricted(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"allowed_providers": ["evren"]}))
    monkeypatch.setattr(policy, "POLICY_PATH", path)
    return path

def test_policy_blocks_other_providers_and_corruption(restricted):
    assert _backend_allows({"provider": "evren", "runtime": "opencode"}, "cao")
    assert not _backend_allows({"provider": "codex"}, "cao")
    restricted.write_text("broken")
    assert not policy.model_allowed({"provider": "evren"})

@pytest.mark.asyncio
async def test_direct_and_cao_deny_non_evren_before_launch(restricted, tmp_path):
    model = {"id": "blocked", "provider": "codex"}
    with pytest.raises(ProviderError):
        await Providers([model], tmp_path).run("blocked", "hello", workspace=str(tmp_path), allow_writes=True, timeout=30, on_event=AsyncMock())
    with pytest.raises(CAOError):
        await CAOClient(tmp_path, tmp_path).run(model, "hello", workspace=str(tmp_path), allow_writes=True, timeout=30, on_event=AsyncMock())

@pytest.mark.asyncio
async def test_evren_child_dispatch_preserves_readonly(restricted, tmp_path, monkeypatch):
    adapter = AsyncMock(return_value={"output": "Reviewed", "model_id": "evren-test", "status": "completed"})
    monkeypatch.setattr(Providers, "run", adapter)
    client = CAOClient(tmp_path, tmp_path)
    result = await client.run_worker("code_reviewer", "Review", {"id": "evren-test", "provider": "evren"}, workspace=str(tmp_path), allow_writes=True, timeout=30, on_event=AsyncMock())
    assert result["output"] == "Reviewed"
    assert adapter.call_count == 1
    assert adapter.call_args.kwargs["allow_writes"] is False

@pytest.mark.asyncio
async def test_evren_executor_never_falls_through_to_cao(restricted, tmp_path):
    from manager.schema import Step
    model = {"id": "evren-test", "provider": "evren"}
    engine = Engine.__new__(Engine)
    engine.models = {"evren-test": model}
    engine.providers = SimpleNamespace(run=AsyncMock(return_value={"output": "done", "model_id": "evren-test", "status": "completed"}))
    engine.cao = SimpleNamespace(run=AsyncMock(side_effect=AssertionError("unexpected CAO call")))
    engine.router = SimpleNamespace(get_cooldowns=AsyncMock(return_value={}), record_success=AsyncMock())
    run = {"id": "test", "execution_backend": "cao", "allow_writes": True, "workspace": str(tmp_path), "timeout_seconds": 30}
    result = await engine.run_executor(run, Step(id="one", title="Implement", prompt="Implement", model_id="evren-test", persona="developer"), "Implement", on_event=AsyncMock())
    assert result["output"] == "done"
    assert engine.providers.run.call_count == 1
    engine.cao.run.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize("writes", [False, True])
async def test_opencode_uses_installed_flags_and_reports_cli_errors(restricted, tmp_path, monkeypatch, writes):
    from manager import providers
    model = {"id": "evren-test", "provider": "evren", "base_model": "evren/deepseek-v4.1-flash"}
    launch = AsyncMock(return_value={"stdout": "", "stderr": "unknown flag", "returncode": 2})
    monkeypatch.setattr(providers, "resolve_opencode", lambda: ["opencode"])
    monkeypatch.setattr(providers, "run_command", launch)
    with pytest.raises(ProviderError):
        await Providers([model], tmp_path).run("evren-test", "hello", workspace=str(tmp_path), allow_writes=writes, timeout=30, on_event=AsyncMock())
    command = launch.call_args.args[0]
    assert "--yes" not in command
    assert ("--auto" in command) is writes
    assert ("plan" in command) is (not writes)
    assert ("build" in command) is writes

@pytest.mark.asyncio
async def test_blocked_catalog_alias_does_not_disable_evren_runtime(restricted):
    engine = Engine.__new__(Engine)
    engine.models = {"opencode": {"id": "opencode", "provider": "opencode", "runtime": "opencode"}, "evren-test": {"id": "evren-test", "provider": "evren", "runtime": "opencode"}}
    engine.providers = SimpleNamespace(available=lambda: [{"id": "opencode", "available": True}])
    availability = await engine.model_availability()
    from manager.model_router import _availability_ok
    assert availability["opencode"] is True
    assert _availability_ok("evren-test", engine.models["evren-test"], availability)
    assert not _backend_allows(engine.models["opencode"], "cao")


def test_nested_cli_timeout_is_not_misclassified_as_quota():
    from manager.outcomes import is_timeout_error
    try:
        try:
            raise TimeoutError()
        except TimeoutError as cause:
            raise ProviderError("localized failure") from cause
    except ProviderError as error:
        assert is_timeout_error(error)
    assert is_timeout_error(ProviderError("CLI toplam süre sınırını aştı."))
    assert not is_timeout_error(ProviderError("Sağlayıcı kullanım kotası doldu."))

@pytest.mark.asyncio
async def test_invalid_audit_json_tries_next_model_without_accepting_it():
    engine = Engine.__new__(Engine)
    engine.models = {"bad": {"id": "bad", "provider": "evren"}, "good": {"id": "good", "provider": "evren"}}
    engine.model_availability = AsyncMock(return_value={"bad": True, "good": True})
    engine.router = SimpleNamespace(rank=AsyncMock(return_value=[SimpleNamespace(model_id="bad"), SimpleNamespace(model_id="good")]), get_cooldowns=AsyncMock(return_value={}), record_success=AsyncMock(), record_failure=AsyncMock())
    engine.cao = SimpleNamespace(run=AsyncMock(side_effect=[{"output": '{"action":"accept","reason":"unchecked"} trailing prose'}, {"output": '{"action":"repair","reason":"Missing test evidence","repair_goal":"Run tests"}'}]))
    engine.optimizer = SimpleNamespace(estimate_tokens=lambda _: 10, record_usage=AsyncMock())
    engine.measured_tokens = lambda *_: (10, 10)
    engine.record_result_usage = AsyncMock()
    engine.emitter = lambda *_: AsyncMock()
    engine._local_reader_models = lambda _: []
    run = {"id": "test", "execution_backend": "cao", "workspace": ".", "timeout_seconds": 30}
    result = await engine._architect_json(run, "Audit", "phase_audit:0:1")
    assert result["model_id"] == "good"
    assert engine.cao.run.call_count == 2
    engine.router.record_success.assert_awaited_once_with("good")
    engine.router.record_failure.assert_awaited_once()


def test_opencode_json_transport_ignores_tools_and_prior_messages():
    events = [
        {"type": "text", "part": {"id": "old", "messageID": "m1", "text": "I will inspect files"}},
        {"type": "tool_use", "part": {"text": "Tool output and non-JSON banners"}},
        {"type": "text", "part": {"id": "last", "messageID": "m2", "text": "partial"}},
        {"type": "text", "part": {"id": "last", "messageID": "m2", "text": '{"action":"repair","reason":"missing tests","repair_goal":"Run tests"}'}},
    ]
    output = Providers._opencode("evren-test", {"stdout": "\n".join(json.dumps(e) for e in events)})["output"]
    from manager.program import parse_completion_audit
    assert parse_completion_audit(output).action == "repair"
    assert "Tool output" not in output

@pytest.mark.parametrize("event", [{"type":"error"}, {"type":"step_start"}])
def test_opencode_json_transport_rejects_errors_or_no_message(event):
    with pytest.raises(ProviderError):
        Providers._opencode("evren-test", {"stdout": json.dumps(event)})


@pytest.mark.asyncio
async def test_retry_refreshes_checkpoint_and_dependent_verification():
    from unittest.mock import Mock
    run = {"id":"test", "execution_id":"old", "current_steps":[], "plan":{"steps":[{"id":"write","model_id":"evren-test"},{"id":"verify","dependencies":["write"]},{"id":"other"}]}, "results":{key:{"status":"completed"} for key in ["write","verify","other"]}}
    engine = Engine.__new__(Engine)
    engine.cancel_quota_resume = Mock()
    engine.store = SimpleNamespace(get=AsyncMock(return_value=run),event=AsyncMock())
    engine.tasks = {}
    engine.router = SimpleNamespace(get_cooldowns=AsyncMock(return_value={}))
    engine.change = AsyncMock()
    engine.start = Mock()
    await engine.retry_step("test", "write")
    change = engine.change.call_args.kwargs
    assert set(change["results"]) == {"other"}
    assert change["execution_id"] != "old"
    engine.start.assert_called_once_with("test", execute=True)

@pytest.mark.asyncio
async def test_planner_repairs_invalid_json_before_returning_plan():
    engine = Engine.__new__(Engine)
    engine.models = {"evren": {"id":"evren", "provider":"evren"}}
    engine.model_availability = AsyncMock(return_value={"evren":True})
    engine.router = SimpleNamespace(rank=AsyncMock(return_value=[]), get_cooldowns=AsyncMock(return_value={}), record_success=AsyncMock(), record_failure=AsyncMock())
    engine.providers = SimpleNamespace(run=AsyncMock(side_effect=[{"output":'{"steps":[broken]}'}, {"output":json.dumps({"steps":[{"id":"one","title":"One","prompt":"Do one thing","model_id":"auto"}]})}]))
    engine.store = SimpleNamespace(event=AsyncMock())
    engine.record_result_usage = AsyncMock()
    engine.emitter = lambda *_: AsyncMock()
    run={"id":"test","execution_backend":"cao","workspace":".","timeout_seconds":30}
    plan, model = await engine.generate_valid_plan(run, "Task", "evren")
    assert model == "evren" and plan.steps[0].id == "one"
    assert engine.providers.run.call_count == 2
    assert "ONARIM" in engine.providers.run.call_args.args[1]
    assert any(call.args[1] == "planner_recovered" for call in engine.store.event.call_args_list)

@pytest.mark.asyncio
async def test_planner_repair_is_bounded_and_never_accepts_bad_json():
    engine = Engine.__new__(Engine)
    engine.models = {mid:{"id":mid,"provider":"evren"} for mid in ["a","b","c"]}
    engine.model_availability = AsyncMock(return_value={mid:True for mid in engine.models})
    engine.router = SimpleNamespace(rank=AsyncMock(return_value=[SimpleNamespace(model_id=x) for x in ["a","b","c"]]), get_cooldowns=AsyncMock(return_value={}), record_success=AsyncMock(), record_failure=AsyncMock())
    engine.providers=SimpleNamespace(run=AsyncMock(return_value={"output":'{"steps":[broken]}'}))
    engine.store=SimpleNamespace(event=AsyncMock())
    engine.record_result_usage = AsyncMock()
    engine.emitter=lambda *_: AsyncMock()
    with pytest.raises(ValueError, match="4 sinirli"):
        await engine.generate_valid_plan({"id":"test","execution_backend":"cao","workspace":".","timeout_seconds":30}, "Task", "a")
    assert engine.providers.run.call_count == 4
    engine.router.record_success.assert_not_called()


@pytest.mark.asyncio
async def test_evren_plan_repairs_unsupported_mcp_before_executor_selection():
    engine = Engine.__new__(Engine)
    engine.models = {"evren": {"id":"evren","provider":"evren"}}
    engine.model_availability = AsyncMock(return_value={"evren":True})
    engine.router = SimpleNamespace(rank=AsyncMock(return_value=[]),get_cooldowns=AsyncMock(return_value={}),record_success=AsyncMock(),record_failure=AsyncMock())
    step={"id":"one","title":"Inspect","prompt":"Inspect local files","persona":"architect","mcp_servers":["sequential-thinking"]}
    engine.providers=SimpleNamespace(run=AsyncMock(side_effect=[{"output":json.dumps({"steps":[step]})},{"output":json.dumps({"steps":[{**step,"mcp_servers":[]}]})}]))
    engine.store=SimpleNamespace(event=AsyncMock())
    engine.record_result_usage = AsyncMock()
    engine.emitter=lambda *_: AsyncMock()
    plan,_=await engine.generate_valid_plan({"id":"test","execution_backend":"cao","workspace":".","timeout_seconds":30},"Task","evren")
    assert plan.steps[0].mcp_servers == []
    assert engine.providers.run.call_count == 2
    assert "MCP yurutucusu yok" in engine.providers.run.call_args.args[1]
