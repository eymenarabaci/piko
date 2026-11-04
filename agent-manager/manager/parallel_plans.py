"""Flatten independently executable plans without introducing group barriers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re

from .schema import Plan


_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def flatten_parallel_plans(specs: Sequence[Mapping]) -> tuple[Plan, dict[str, dict]]:
    """Return a namespaced Plan and original metadata indexed by flat step ID.

    Each spec is ``{id, plan: Plan | dict, depends_on?: list[plan_id]}``.
    Original metadata is ``{plan_id, step_id, step: original_step_dict}``.
    Only a dependent plan's roots receive the named plans' terminal step IDs;
    independent plans share no extra edges. Schema size limits fail up front.
    """
    if isinstance(specs, (str, bytes)) or not isinstance(specs, Sequence) or not 1 <= len(specs) <= 6:
        raise ValueError("Expected between 1 and 6 parallel plan specs")
    plans, dependencies = {}, {}
    for spec in specs:
        if not isinstance(spec, Mapping):
            raise ValueError("Parallel plan spec must be a mapping")
        plan_id = spec.get("id")
        if not isinstance(plan_id, str) or not _ID.fullmatch(plan_id):
            raise ValueError("Invalid parallel plan ID")
        if plan_id in plans:
            raise ValueError(f"Duplicate parallel plan ID: {plan_id}")
        deps = spec.get("depends_on", [])
        if (not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps)
                or len(set(deps)) != len(deps)):
            raise ValueError("Invalid or duplicate parallel plan dependencies")
        # Revalidate Plan instances as well, even if a caller used model_copy
        # or model_construct to bypass the original schema's validation.
        raw = spec.get("plan")
        plans[plan_id] = Plan.model_validate(raw.model_dump() if isinstance(raw, Plan) else raw)
        dependencies[plan_id] = set(deps)
    unknown = set().union(*dependencies.values()) - set(plans)
    if unknown:
        raise ValueError(f"Unknown parallel plan dependencies: {sorted(unknown)}")
    completed = set()
    while len(completed) < len(plans):
        ready = {key for key, deps in dependencies.items() if key not in completed and deps <= completed}
        if not ready:
            raise ValueError("Parallel plans contain a dependency cycle")
        completed.update(ready)

    names = {}
    original = {}
    for index, (plan_id, plan) in enumerate(plans.items()):
        for step in plan.steps:
            full = f"p{index}_{step.id}"
            if len(full) > 40:
                digest = hashlib.sha256(f"{plan_id}:{step.id}".encode()).hexdigest()[:12]
                full = full[:27] + "_" + digest
            if full in original:
                raise ValueError("Namespaced step ID collision")
            names[plan_id, step.id] = full
            original[full] = {"plan_id": plan_id, "step_id": step.id, "step": step.model_dump()}
    terminals = {}
    for plan_id, plan in plans.items():
        parents = {dep for step in plan.steps for dep in step.dependencies}
        terminals[plan_id] = [names[plan_id, step.id] for step in plan.steps if step.id not in parents]
    flattened = []
    for plan_id, plan in plans.items():
        for step in plan.steps:
            payload = step.model_dump()
            payload["id"] = names[plan_id, step.id]
            payload["dependencies"] = [names[plan_id, dep] for dep in step.dependencies]
            if not step.dependencies:
                payload["dependencies"] += [terminal for predecessor in plans if predecessor in dependencies[plan_id]
                                            for terminal in terminals[predecessor]]
            flattened.append(payload)
    return Plan.model_validate({"steps": flattened}), original
