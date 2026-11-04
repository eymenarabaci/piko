"""Event barriers verify readiness and cleanup without model/service calls."""
import asyncio

import pytest

from manager.dag_dispatch import execute_dag


def result(key, **extra):
    return {"results": {key: {"status": "completed", **extra}}}


@pytest.mark.asyncio
async def test_child_starts_before_unrelated_slow_sibling_finishes():
    slow_started, release_slow, child_started = (asyncio.Event() for _ in range(3))
    active = maximum = 0

    async def run(key, state):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            if key == "a":
                await slow_started.wait()
            elif key == "c":
                slow_started.set()
                await release_slow.wait()
            else:
                assert "a" in state["results"]
                assert "c" not in state["results"]
                assert state["prompt"] == "keep context"
                assert not release_slow.is_set()
                child_started.set()
            return result(key)
        finally:
            active -= 1

    nodes = {key: (lambda state, key=key: run(key, state)) for key in ["a", "c", "b"]}
    task = asyncio.create_task(execute_dag({"a": [], "c": [], "b": ["a"]}, nodes,
                                         {"prompt": "keep context"}))
    try:
        await asyncio.wait_for(child_started.wait(), 2)
        assert not task.done()
        release_slow.set()
        output = await asyncio.wait_for(task, 2)
        assert set(output["results"]) == {"a", "b", "c"}
        assert maximum == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_parallelism_honors_requested_limit_of_two():
    both_started, release = asyncio.Event(), asyncio.Event()
    started = []
    active = maximum = 0

    async def run(key, state):
        nonlocal active, maximum
        started.append(key)
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            both_started.set()
        try:
            await release.wait()
            return result(key)
        finally:
            active -= 1

    keys = ["a", "b", "c", "d"]
    task = asyncio.create_task(execute_dag({k: [] for k in keys},
        {k: (lambda state, key=k: run(key, state)) for k in keys}, max_parallel=2))
    try:
        await asyncio.wait_for(both_started.wait(), 2)
        assert len(started) == 2
        release.set()
        await asyncio.wait_for(task, 2)
        assert maximum == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failure_cancels_and_awaits_sibling_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()
    child_called = False

    async def fail(state):
        await started.wait()
        raise RuntimeError("failed parent")

    async def sibling(state):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def child(state):
        nonlocal child_called
        child_called = True
        return result("child")

    with pytest.raises(RuntimeError, match="failed parent"):
        await asyncio.wait_for(execute_dag(
            {"a": [], "c": [], "child": ["a"]},
            {"a": fail, "c": sibling, "child": child}), 2)
    assert cleaned.is_set()
    assert not child_called


@pytest.mark.asyncio
async def test_completed_results_skip_execution_but_failed_results_retry():
    saved = {"done": {"status": "completed", "output": "persisted"},
             "retry": {"status": "failed"}}
    called = []

    async def never(state):
        raise AssertionError("completed node must be skipped")

    async def retry(state):
        called.append("retry")
        assert state["results"]["done"]["output"] == "persisted"
        return result("retry")

    output = await execute_dag({"done": [], "retry": ["done"]},
                               {"done": never, "retry": retry}, {"results": saved})
    assert called == ["retry"]
    assert output["results"]["done"] == saved["done"]
    assert output["results"]["retry"]["status"] == "completed"
    assert saved["retry"]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("deps, message", [
    ({"a": ["missing"]}, "Missing DAG dependencies"),
    ({"a": ["a"]}, "cycle"),
    ({"a": ["b"], "b": ["a"]}, "cycle"),
])
async def test_invalid_graph_never_starts_a_node(deps, message):
    async def forbidden(state):
        raise AssertionError("invalid graph must not start")
    with pytest.raises(ValueError, match=message):
        await execute_dag(deps, {key: forbidden for key in deps})


@pytest.mark.asyncio
async def test_external_cancellation_awaits_all_children():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def child(state):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(execute_dag({"a": []}, {"a": child}))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
