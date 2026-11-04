"""Capability derivation and user-triggered calibration for model triples.

Two separate things live here:

* ``derive_capabilities`` is automatic, free and runs during discovery.
* Calibration is never automatic. A triple is only probed when the operator
  explicitly asks for it (one row or a bulk selection). An uncalibrated triple
  stays unweighted and is ranked from capabilities/provenance alone.

Calibration uses a fixed, tiny task set and a small budget profile. The actual
model invocation is injected so tests can run completely offline.
"""
from __future__ import annotations

import asyncio
import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import json
import math
import re
from typing import Callable, Iterable, Mapping, Sequence

from .model_discovery import account_name, model_name, provider_runtime, triple_key

ROLES = ("development", "architect", "reviewer", "general")
BUDGET_PROFILE = {
    "name": "micro",
    "max_tasks": 5,
    "timeout_seconds": 45,
    "max_output_tokens": 256,
}


@dataclass(frozen=True, slots=True)
class MicroTask:
    id: str
    role: str
    prompt: str
    required_modalities: tuple[str, ...] = ("text",)
    expect_any: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "required_modalities": list(self.required_modalities),
        }


MICRO_TASKS: tuple[MicroTask, ...] = (
    MicroTask(
        id="code_fix",
        role="development",
        prompt=(
            "Fix this Python function and return only the corrected code:\n"
            "def add(a, b):\n    return a - b\n"
        ),
        expect_any=("return a + b", "a + b"),
    ),
    MicroTask(
        id="json_plan",
        role="architect",
        prompt=(
            "Return only valid JSON with exactly two steps. First id=inspect, dependencies=[]. "
            "Second id=summarize, dependencies=[inspect]. Each step needs a nonempty title. "
            'Shape: {"steps":[{"id":"inspect","title":"...","dependencies":[]},'
            '{"id":"summarize","title":"...","dependencies":["inspect"]}]}'
        ),
        expect_any=("{", "steps"),
    ),
    MicroTask(
        id="summarize",
        role="reviewer",
        prompt=(
            "Review this Python function intended to return the first element of a nonempty list:\n"
            "1: def first(xs):\n2:     return xs[1]\n"
            "Return only JSON with bug_line (integer) and fix (the corrected return statement)."
        ),
        expect_any=("timeout", "build", "failed"),
    ),
    MicroTask(
        id="vision",
        role="general",
        prompt="Describe the dominant colour in this image in one word.",
        required_modalities=("text", "image"),
        expect_any=("white", "beyaz"),
    ),
    MicroTask(
        id="latency",
        role="general",
        prompt="Reply with the single word OK.",
        expect_any=("ok",),
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _modalities(metadata: Mapping) -> set[str]:
    values = metadata.get("input_modalities") or metadata.get("modalities") or ["text"]
    return {str(value).casefold() for value in values}


def derive_capabilities(metadata: Mapping) -> tuple[list[str], dict[str, float]]:
    """Derive role capabilities from cheap catalog metadata only."""
    name = str(metadata.get("model") or metadata.get("base_model") or metadata.get("id") or "").casefold()
    modalities = _modalities(metadata)
    context = metadata.get("context_window_tokens") or metadata.get("context_window")
    if isinstance(context, bool) or not isinstance(context, (int, float)):
        context = None
    supports_writes = metadata.get("supports_writes", True)
    supports_tools = metadata.get("mcp_supported", metadata.get("supports_tools", True))

    vision_only = ("image" in modalities and "text" not in modalities) or any(
        marker in name for marker in ("moondream", "llava", "bakllava", "minicpm-v"))
    capabilities: set[str] = {"general"}
    if "image" in modalities:
        capabilities.add("image_analysis")
    if not vision_only:
        capabilities.update({"development", "reviewer"})
        if context is None or context >= 8192:
            capabilities.add("architect")
        if supports_writes is False:
            capabilities.discard("development")
        if "code" in name or "coder" in name or "dev" in name:
            capabilities.add("development")
        if "reason" in name or "plan" in name or "opus" in name:
            capabilities.add("architect")
        if supports_tools is False:
            capabilities.discard("reviewer")
    ordered = [role for role in ROLES if role in capabilities]
    priority = {role: 20.0 for role in ordered}
    return ordered, priority


def tasks_for(record: Mapping, tasks: Sequence[MicroTask] = MICRO_TASKS) -> list[MicroTask]:
    modalities = _modalities(record)
    selected = []
    for task in tasks:
        if any(value not in modalities for value in task.required_modalities):
            continue
        selected.append(task)
    return selected[: int(BUDGET_PROFILE["max_tasks"])]


def evaluate_micro_task(task: MicroTask, output: str) -> bool:
    if not isinstance(output, str) or not output.strip():
        return False
    value = output.strip()
    if len(value) > 12_000:
        return False
    if value.startswith('```'):
        match = re.fullmatch(r'```(?:python|json)?\s*\n?(.*?)\n?```', value, re.DOTALL)
        if not match:
            return False
        value = match.group(1).strip()
    try:
        if task.id == 'code_fix':
            # Inspect and evaluate a tiny arithmetic AST ourselves. Model code
            # is never executed, imported or passed to eval/exec.
            module = ast.parse(value)
            if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
                return False
            fn = module.body[0]
            args = fn.args
            body = fn.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            annotations = [fn.returns, *(arg.annotation for arg in args.args)]
            safe_annotations = all(annotation is None or isinstance(annotation, ast.Name)
                                   and annotation.id in {'int', 'float'} for annotation in annotations)
            if (fn.name != 'add' or fn.decorator_list or len(body) != 1
                    or not isinstance(body[0], ast.Return)
                    or [arg.arg for arg in args.args] != ['a', 'b']
                    or not safe_annotations
                    or args.posonlyargs or args.kwonlyargs or args.defaults
                    or args.vararg or args.kwarg):
                return False
            expression = body[0].value
            if len(list(ast.walk(expression))) > 80:
                return False
            def arithmetic(node, values):
                if isinstance(node, ast.Name) and node.id in values:
                    return values[node.id]
                if isinstance(node, ast.Constant) and type(node.value) is int and abs(node.value) <= 1000:
                    return node.value
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                    number = arithmetic(node.operand, values)
                    return number if isinstance(node.op, ast.UAdd) else -number
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                    left, right = arithmetic(node.left, values), arithmetic(node.right, values)
                    if abs(left) > 10**12 or abs(right) > 10**12:
                        raise ValueError('Arithmetic probe bound exceeded')
                    return left + right if isinstance(node.op, ast.Add) else left - right if isinstance(node.op, ast.Sub) else left * right
                raise ValueError('Unsupported probe expression')
            return all(arithmetic(expression, {'a': a, 'b': b}) == a + b
                       for a, b in [(2, 3), (-7, 2), (0, 0), (9, -4), (123, 456)])
        if task.id == 'json_plan':
            payload = json.loads(value)
            if not isinstance(payload, dict) or set(payload) != {'steps'}:
                return False
            steps = payload['steps']
            if not isinstance(steps, list) or len(steps) != 2:
                return False
            for step, step_id, dependencies in zip(steps, ['inspect', 'summarize'], [[], ['inspect']]):
                if (not isinstance(step, dict) or set(step) != {'id', 'title', 'dependencies'}
                        or step['id'] != step_id or step['dependencies'] != dependencies
                        or not isinstance(step['title'], str) or not step['title'].strip()):
                    return False
            return True
        if task.id == 'summarize':
            payload = json.loads(value)
            if (not isinstance(payload, dict) or set(payload) != {'bug_line', 'fix'}
                    or type(payload['bug_line']) is not int or payload['bug_line'] != 2
                    or not isinstance(payload['fix'], str)):
                return False
            fix = ast.parse(payload['fix'].strip())
            expected = ast.parse('return xs[0]')
            return ast.dump(fix) == ast.dump(expected)
        if task.id == 'vision':
            return value.casefold() in {'white', 'beyaz'}
        if task.id == 'latency':
            return value.casefold() == 'ok'
        return bool(task.expect_any) and value.casefold() in {expected.casefold() for expected in task.expect_any}
    except (ValueError, TypeError, SyntaxError, RecursionError, OverflowError):
        return False


def smooth_weight(current: float | None, passed: bool, *, alpha: float = 0.25) -> float:
    """Exponential smoothing toward a verified 0/1 outcome."""
    if alpha <= 0 or alpha > 1:
        raise ValueError("alpha 0 ile 1 arasında olmalı")
    prior = 0.5 if current is None else float(current)
    prior = min(1.0, max(0.0, prior))
    observed = 1.0 if passed else 0.0
    return round((1.0 - alpha) * prior + alpha * observed, 6)


def weights_from_results(
    results: Iterable[Mapping],
    tasks: Sequence[MicroTask] = MICRO_TASKS,
) -> dict[str, float]:
    """Compute role weights from fake or real probe results."""
    by_id = {task.id: task for task in tasks}
    grouped: dict[str, list[Mapping]] = {}
    for row in results:
        task = by_id.get(str(row.get("task_id") or row.get("id") or ""))
        role = str(row.get("role") or (task.role if task else "general"))
        grouped.setdefault(role, []).append(row)
    weights: dict[str, float] = {}
    for role, rows in grouped.items():
        total = len(rows)
        if not total:
            continue
        passed = sum(1 for row in rows if bool(row.get("passed")))
        durations = [float(row.get("duration_seconds", 0) or 0) for row in rows]
        mean_duration = sum(durations) / total if durations else 0.0
        latency = 30.0 / (30.0 + max(0.0, mean_duration))
        weight = 0.75 * (passed / total) + 0.25 * latency
        weights[role] = round(min(1.0, max(0.0, weight)), 6)
    if weights:
        weights["overall"] = round(sum(weights.values()) / len(weights), 6)
    return weights


async def _emit(progress: Callable[[dict], object] | None, event: dict) -> None:
    if progress is None:
        return
    outcome = progress(event)
    if inspect.isawaitable(outcome):
        await outcome


def _record_key(record: Mapping) -> tuple[str, str, str]:
    runtime = str(record.get("runtime") or provider_runtime(record.get("provider"), record.get("cli")))
    return (model_name(record), runtime, account_name(record))


def _find_record(records: Sequence[dict], triple: Mapping) -> int | None:
    wanted_id = triple.get("id")
    if wanted_id:
        for index, record in enumerate(records):
            if record.get("id") == wanted_id:
                return index
    wanted = (
        str(triple.get("model") or triple.get("base_model") or "").strip(),
        str(triple.get("runtime") or "").strip(),
        str(triple.get("account") or "default").strip(),
    )
    for index, record in enumerate(records):
        if _record_key(record) == wanted:
            return index
    return None


async def calibrate_triple(
    record: Mapping,
    runner: Callable[[Mapping, MicroTask, Mapping], object],
    *,
    budget: Mapping | None = None,
    progress: Callable[[dict], object] | None = None,
    cancel: asyncio.Event | None = None,
    tasks: Sequence[MicroTask] = MICRO_TASKS,
) -> dict:
    """Run the fixed micro-task set for one triple; never raises on task errors."""
    profile = dict(BUDGET_PROFILE if budget is None else budget)
    selected = tasks_for(record, tasks)[: int(profile.get("max_tasks", 5))]
    results: list[dict] = []
    errors = 0
    for task in selected:
        if cancel is not None and cancel.is_set():
            await _emit(progress, {"type": "cancelled", "task_id": task.id,
                                   "triple": triple_key(*_record_key(record))})
            return {"status": "cancelled", "results": results}
        started = asyncio.get_running_loop().time()
        await _emit(progress, {"type": "task_start", "task_id": task.id, "role": task.role,
                               "triple": triple_key(*_record_key(record))})
        try:
            outcome = runner(record, task, profile)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            if cancel is not None and cancel.is_set():
                await _emit(progress, {"type": "cancelled", "task_id": task.id,
                                       "triple": triple_key(*_record_key(record))})
                return {"status": "cancelled", "results": results}
            if not isinstance(outcome, Mapping):
                outcome = {}
            duration = outcome.get("duration_seconds")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
                duration = asyncio.get_running_loop().time() - started
            passed = outcome.get("passed") is True
            if "output" in outcome:
                passed = evaluate_micro_task(task, str(outcome.get("output") or ""))
            row = {"task_id": task.id, "role": task.role, "passed": passed,
                   "duration_seconds": float(duration)}
            if outcome.get("error"):
                row["error"] = str(outcome["error"])[:200]
            results.append(row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a probe failure is data, not a crash
            errors += 1
            results.append({"task_id": task.id, "role": task.role, "passed": False,
                            "duration_seconds": asyncio.get_running_loop().time() - started,
                            "error": f"{type(exc).__name__}: {exc}"[:200]})
        await _emit(progress, {"type": "task_done", "task_id": task.id,
                               "passed": results[-1]["passed"],
                               "triple": triple_key(*_record_key(record))})
    if not results or errors == len(results):
        return {"status": "incomplete", "results": results}
    weights = weights_from_results(results, tasks)
    return {"status": "calibrated", "results": results, "role_weights": weights}


async def calibrate_triples(
    records: Iterable[Mapping],
    triples: Iterable[Mapping],
    runner: Callable[[Mapping, MicroTask, Mapping], object],
    *,
    budget: Mapping | None = None,
    progress: Callable[[dict], object] | None = None,
    cancel: asyncio.Event | None = None,
) -> tuple[list[dict], dict]:
    """Calibrate only the explicitly requested triples.

    Discovery must never call this function. Callers pass a concrete triple
    list from a user action.
    """
    updated = [dict(record) for record in records]
    report: dict = {"requested": 0, "calibrated": [], "skipped": [], "cancelled": []}
    requested = list(triples)
    report["requested"] = len(requested)
    for triple in requested:
        index = _find_record(updated, triple)
        if index is None:
            report["skipped"].append({**dict(triple), "reason": "triple_not_found"})
            continue
        record = updated[index]
        await _emit(progress, {"type": "triple_start", "id": record.get("id"),
                               "model": model_name(record), "runtime": record.get("runtime")})
        outcome = await calibrate_triple(record, runner, budget=budget,
                                         progress=progress, cancel=cancel)
        if outcome["status"] == "cancelled":
            report["cancelled"].append(record.get("id"))
            await _emit(progress, {"type": "triple_cancelled", "id": record.get("id")})
            break
        if outcome["status"] != "calibrated":
            report["skipped"].append({"id": record.get("id"), "reason": outcome["status"]})
            await _emit(progress, {"type": "triple_skipped", "id": record.get("id"),
                                   "reason": outcome["status"]})
            continue
        weights = outcome["role_weights"]
        provenance = dict(record.get("provenance") or {})
        provenance["calibrated"] = True
        provenance["provenance"] = "auto_calibrated"
        provenance["calibration_budget"] = dict(budget or BUDGET_PROFILE)
        record["role_weights"] = weights
        record["calibrated_at"] = utc_now()
        record["provenance"] = provenance
        record["calibration"] = {"status": "calibrated", "results": outcome["results"],
                                 "budget": dict(budget or BUDGET_PROFILE)}
        updated[index] = record
        report["calibrated"].append(record.get("id"))
        await _emit(progress, {"type": "triple_done", "id": record.get("id"),
                               "role_weights": weights})
    return updated, report


def apply_outcome_smoothing(record: Mapping, role: str, passed: bool,
                            *, alpha: float = 0.25) -> dict:
    """Return a copy with live outcome signal folded into role weights."""
    row = dict(record)
    weights = dict(row.get("role_weights") or {})
    weights[role] = smooth_weight(weights.get(role), passed, alpha=alpha)
    row["role_weights"] = weights
    samples = dict(row.get("weight_samples") or {})
    samples[role] = int(samples.get(role, 0)) + 1
    row["weight_samples"] = samples
    return row


def is_calibrated(record: Mapping) -> bool:
    provenance = record.get("provenance") or {}
    return bool(record.get("role_weights")) and (
        provenance.get("calibrated") is True or provenance.get("provenance") == "auto_calibrated"
    )


def role_weight(record: Mapping, role: str) -> float | None:
    weights = record.get("role_weights")
    if not isinstance(weights, Mapping) or not is_calibrated(record):
        return None
    value = weights.get(role, weights.get("overall"))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)
