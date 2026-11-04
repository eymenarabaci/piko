"""Dispatch ready DAG nodes without a global sibling/superstep barrier."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

Node = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]


async def execute_dag(
    dependencies: Mapping[str, Iterable[str]],
    nodes: Mapping[str, Node],
    state: Mapping[str, Any] | None = None,
    *,
    max_parallel: int = 2,
    capacity_pool: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Execute ready nodes, passing completed results in independent snapshots.

    Nodes return ``{"results": {node_id: result}}``. Previously completed or
    successful results are retained without invoking their nodes. Graph
    validation runs before any node starts. Persistence belongs to each node;
    this dispatcher only controls readiness, merging and child-task lifetime.
    An optional shared semaphore caps actual execution across dispatchers.
    """
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or not 1 <= max_parallel <= 6:
        raise ValueError("max_parallel must be an integer between 1 and 6")
    if capacity_pool is not None and not isinstance(capacity_pool, asyncio.Semaphore):
        raise ValueError("capacity_pool must be an asyncio.Semaphore")
    limit = max_parallel
    deps = {key: set(values) for key, values in dependencies.items()}
    if set(nodes) != set(deps):
        raise ValueError("DAG nodes and dependency keys must match")
    missing = set().union(*deps.values()) - set(deps) if deps else set()
    if missing:
        raise ValueError(f"Missing DAG dependencies: {sorted(missing)}")
    # Check even already-completed subgraphs: bad input must never run partly.
    visited = set()
    while len(visited) < len(deps):
        ready = {key for key, parents in deps.items() if key not in visited and parents <= visited}
        if not ready:
            raise ValueError("DAG contains a dependency cycle")
        visited.update(ready)

    base = dict(state or {})
    results = dict(base.get("results") or {})
    complete = {key for key, result in results.items()
                if key in deps and isinstance(result, Mapping)
                and result.get("status") in {"completed", "success"}}
    pending = set(deps) - complete
    running: dict[asyncio.Task, str] = {}

    async def invoke(key: str, snapshot: dict[str, Any]):
        # Only dependency-ready nodes reach this point. Capacity is held for
        # actual execution, never while waiting for a predecessor's result.
        if capacity_pool is None:
            return await nodes[key](snapshot)
        async with capacity_pool:
            return await nodes[key](snapshot)

    try:
        while pending or running:
            for key in deps:
                if len(running) >= limit:
                    break
                if key not in pending or not deps[key] <= complete:
                    continue
                pending.remove(key)
                snapshot = {**base, "results": dict(results)}
                task = asyncio.create_task(invoke(key, snapshot), name=f"dag:{key}")
                running[task] = key
            if not running:
                raise ValueError("DAG cannot progress")
            finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            # Read all completed tasks before launching any further children.
            for task in tuple(running):
                if task not in finished:
                    continue
                key = running[task]
                update = task.result()
                delta = update.get("results") if isinstance(update, Mapping) else None
                if not isinstance(delta, Mapping) or key not in delta:
                    raise ValueError(f"DAG node {key!r} did not return its result")
                results.update(delta)
                complete.add(key)
                del running[task]
    finally:
        # This also drains exceptions when multiple siblings fail together.
        if running:
            for task in running:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
    return {"results": results}
