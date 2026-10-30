"""No provider calls: quota-aware execution contracts and delegation validation."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from manager.outcomes import InfrastructureError, ensure_usable_result, is_infrastructure_error
from manager.worker_catalog import WorkerRequestError, extract_worker_calls, worker_request_instructions


def test_worker_placeholders_and_quoted_protocol_are_not_executed():
    assert 'Kısa alt görev' not in worker_request_instructions()
    with pytest.raises(WorkerRequestError, match='yer tutucusu'):
        extract_worker_calls('<cao_workers>{"calls":[{"role":"planner","task":"Kısa alt görev"}]}</cao_workers>')
    with pytest.raises(WorkerRequestError, match='alıntı'):
        extract_worker_calls('Örnek: <cao_workers>{"calls":[{"role":"planner","task":"Giriş akışını planla"}]}</cao_workers>')


def test_missing_tool_runtime_is_not_a_success():
    with pytest.raises(InfrastructureError):
        ensure_usable_result({'output': 'codex-code-mode-host.exe bulunamadığı için hiçbir dosya değiştirilmedi.'})
    assert not is_infrastructure_error('Tests fail because user record does not exist')


class NoEligibleModel(RuntimeError):
    pass


def isolated_executor():
    # Isolate orchestration control flow; no engine services or runtime processes.
    source = ast.parse((Path(__file__).parents[1] / 'manager/engine.py').read_text(encoding='utf-8'))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'Engine')
    fn = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run_executor')
    namespace = {'asyncio': asyncio, 'json': __import__('json'), 'NoEligibleModel': NoEligibleModel,
                 'infer_role': lambda *args: 'general', 'is_infrastructure_error': is_infrastructure_error,
                 'ensure_usable_result': ensure_usable_result}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<isolated executor>', 'exec'), namespace)
    return namespace['run_executor']


def executor_state(error):
    event = AsyncMock()
    state = SimpleNamespace(
        models={mid: {'provider': 'codex'} for mid in ('one', 'two', 'three', 'four')},
        providers=SimpleNamespace(run=AsyncMock(side_effect=error)),
        router=SimpleNamespace(record_failure=AsyncMock(return_value=None), record_success=AsyncMock(),
                               get_cooldowns=AsyncMock(return_value={})),
        store=SimpleNamespace(event=event), emitter=lambda *_: event,
        optimizer=SimpleNamespace(estimate_tokens=lambda value: len(value)),
        route=AsyncMock(side_effect=[SimpleNamespace(model_id='two', quota_confidence='unknown'),
                                   SimpleNamespace(model_id='three', quota_confidence='unknown')]),
        set_step_model=AsyncMock(), clear_cooldown=AsyncMock(),
    )
    run = {'id': 'test', 'allow_writes': False, 'execution_backend': 'direct', 'workspace': '.', 'timeout_seconds': 30}
    step = SimpleNamespace(id='step', model_id='one', persona='developer', title='Task', prompt='Task')
    return state, run, step


def complete_immediate(coroutine):
    # All awaited collaborators are immediate AsyncMocks; no event loop or IO.
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected real IO in isolated test')


def test_transient_errors_have_three_attempt_limit_without_cooldown_reset():
    state, run, step = executor_state(RuntimeError('temporary provider failure'))
    with pytest.raises(RuntimeError, match='yürütülemedi'):
        complete_immediate(isolated_executor()(state, run, step, 'Original task'))
    assert state.providers.run.await_count == 3
    state.clear_cooldown.assert_not_awaited()


def test_infrastructure_error_does_not_try_other_models():
    state, run, step = executor_state(FileNotFoundError('failed to spawn code-mode host missing.exe'))
    with pytest.raises(FileNotFoundError):
        complete_immediate(isolated_executor()(state, run, step, 'Original task'))
    assert state.providers.run.await_count == 1
    state.route.assert_not_awaited()
    state.router.record_failure.assert_not_awaited()

