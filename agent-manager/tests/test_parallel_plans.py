"""Cross-plan dependencies and one shared execution pool have no group barrier."""
import asyncio
from copy import deepcopy
import re

import pytest

from manager.dag_dispatch import execute_dag
from manager.parallel_plans import flatten_parallel_plans
from manager.schema import Plan


def plan(*steps):
    return Plan.model_validate({"steps": [{"id": key, "title": key, "prompt": f"Execute {key}",
                                           "dependencies": deps} for key, deps in steps]})


def done(key):
    return {"results": {key: {"status": "completed"}}}


def test_flatten_namespaces_dependencies_and_keeps_original_steps_immutable():
    specs = [
        {"id": "alpha", "plan": plan(("root", []), ("left", ["root"]), ("right", ["root"]))},
        {"id": "beta", "plan": plan(("root", []), ("tail", ["root"])), "depends_on": ["alpha"]},
        {"id": "gamma", "plan": plan(("root", []))},
    ]
    original_specs = deepcopy(specs)
    merged, original = flatten_parallel_plans(specs)
    lookup = {(value["plan_id"], value["step_id"]): key for key, value in original.items()}
    nodes = {step.id: step for step in merged.steps}
    alpha_tail = {lookup["alpha", "left"], lookup["alpha", "right"]}
    assert set(nodes[lookup["beta", "root"]].dependencies) == alpha_tail
    assert nodes[lookup["beta", "tail"]].dependencies == [lookup["beta", "root"]]
    assert nodes[lookup["gamma", "root"]].dependencies == []
    assert original[lookup["alpha", "root"]]["step"]["id"] == "root"
    assert specs == original_specs


def test_max_length_ids_stay_schema_valid_and_collision_free():
    a, b = "a" * 40, "a" * 39 + "b"
    merged, original = flatten_parallel_plans([
        {"id": "p" * 40, "plan": plan((a, []), (b, [a]))},
        {"id": "q" * 40, "plan": plan((a, []))},
    ])
    ids = [step.id for step in merged.steps]
    assert len(set(ids)) == 3
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key) for key in ids)
    assert set(original) == set(ids)


@pytest.mark.parametrize("specs", [
    [],
    [{"id": "Bad-ID", "plan": {"steps": []}}],
    [{"id": "a", "plan": plan(("x", []))}, {"id": "a", "plan": plan(("x", []))}],
    [{"id": "a", "plan": plan(("x", [])), "depends_on": ["missing"]}],
    [{"id": "a", "plan": plan(("x", [])), "depends_on": ["a"]}],
    [{"id": "a", "plan": plan(("x", [])), "depends_on": ["b"]},
     {"id": "b", "plan": plan(("x", [])), "depends_on": ["a"]}],
    [{"id": "a", "plan": plan(("x", [])), "depends_on": ["b", "b"]}],
    [{"id": "a", "plan": {"steps": [{"id": "x", "title": "x", "prompt": "x", "dependencies": ["missing"]}]}}],
])
def test_invalid_missing_or_cyclic_specs_fail_before_dispatch(specs):
    with pytest.raises(ValueError):
        flatten_parallel_plans(specs)


def test_total_schema_step_limit_is_not_silently_truncated():
    specs = [{"id": "a", "plan": plan(*[(f"a{i}", []) for i in range(11)])},
             {"id": "b", "plan": plan(*[(f"b{i}", []) for i in range(11)])}]
    with pytest.raises(ValueError):
        flatten_parallel_plans(specs)


@pytest.mark.asyncio
async def test_fast_plan_child_starts_while_unrelated_plan_sibling_is_running():
    merged, original = flatten_parallel_plans([
        {"id": "fast", "plan": plan(("a", []), ("a2", ["a"]))},
        {"id": "slow", "plan": plan(("b", []))},
    ])
    slow_started, release_slow, child_started = (asyncio.Event() for _ in range(3))
    async def execute(key, state):
        source = original[key]["step_id"]
        if source == "a":
            await slow_started.wait()
        elif source == "b":
            slow_started.set()
            await release_slow.wait()
        else:
            assert not release_slow.is_set()
            assert all(original[parent]["step_id"] != "b" for parent in state["results"])
            child_started.set()
        return done(key)
    task = asyncio.create_task(execute_dag(
        {step.id: step.dependencies for step in merged.steps},
        {step.id: (lambda state, key=step.id: execute(key, state)) for step in merged.steps},
        max_parallel=6, capacity_pool=asyncio.Semaphore(6)))
    try:
        await asyncio.wait_for(child_started.wait(), 2)
        assert not task.done()
        release_slow.set()
        assert len((await asyncio.wait_for(task, 2))["results"]) == 3
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_two_dispatchers_share_six_slots_for_actual_execution():
    capacity = asyncio.Semaphore(6)
    six_started, release = asyncio.Event(), asyncio.Event()
    active = peak = started = 0
    async def execute(key, state):
        nonlocal active, peak, started
        active += 1
        started += 1
        peak = max(peak, active)
        if active == 6:
            six_started.set()
        try:
            await release.wait()
            return done(key)
        finally:
            active -= 1
    tasks = []
    for group in range(2):
        keys = [f"g{group}_{i}" for i in range(8)]
        tasks.append(asyncio.create_task(execute_dag(
            {key: [] for key in keys},
            {key: (lambda state, key=key: execute(key, state)) for key in keys},
            max_parallel=6, capacity_pool=capacity)))
    try:
        await asyncio.wait_for(six_started.wait(), 2)
        assert started == 6
        release.set()
        outputs = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert peak == 6 and started == 16
        assert all(len(output["results"]) == 8 for output in outputs)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_dependency_waiters_never_eagerly_consume_capacity():
    capacity = asyncio.Semaphore(1)
    parent_started, release_parent = asyncio.Event(), asyncio.Event()
    called = []
    # Children deliberately precede their parent in dictionary/plan order.
    deps = {**{f"child_{i}": ["parent"] for i in range(6)}, "parent": []}
    async def execute(key, state):
        called.append(key)
        if key == "parent":
            parent_started.set()
            await release_parent.wait()
        else:
            assert state["results"]["parent"]["status"] == "completed"
        return done(key)
    task = asyncio.create_task(execute_dag(deps,
        {key: (lambda state, key=key: execute(key, state)) for key in deps},
        max_parallel=6, capacity_pool=capacity))
    try:
        await asyncio.wait_for(parent_started.wait(), 2)
        assert called == ["parent"]
        release_parent.set()
        assert len((await asyncio.wait_for(task, 2))["results"]) == 7
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_returns_shared_capacity_to_another_dispatcher():
    capacity = asyncio.Semaphore(1)
    first_started, second_started = asyncio.Event(), asyncio.Event()
    async def first(state):
        first_started.set()
        await asyncio.Event().wait()
    async def second(state):
        second_started.set()
        return done("second")
    one = asyncio.create_task(execute_dag({"first": []}, {"first": first}, capacity_pool=capacity))
    two = None
    try:
        await asyncio.wait_for(first_started.wait(), 2)
        two = asyncio.create_task(execute_dag({"second": []}, {"second": second}, capacity_pool=capacity))
        one.cancel()
        await asyncio.gather(one, return_exceptions=True)
        await asyncio.wait_for(second_started.wait(), 2)
        assert (await asyncio.wait_for(two, 2))["results"]["second"]["status"] == "completed"
    finally:
        one.cancel()
        if two:
            two.cancel()
        await asyncio.gather(*([one, two] if two else [one]), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 7, 99, True, 1.5, "6", None])
async def test_invalid_parallel_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="between 1 and 6"):
        await execute_dag({}, {}, max_parallel=limit)
