import json

import pytest

from manager.model_profiler import MICRO_TASKS, calibrate_triple, evaluate_micro_task, role_weight


TASKS = {task.id: task for task in MICRO_TASKS}


@pytest.mark.parametrize('output', [
    'def add(a, b):\n    return a + b',
    '```python\ndef add(a, b):\n    return (a + b)\n```',
    'def add(a, b):\n    return a - (-b)',
    'def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b',
])
def test_code_probe_checks_arithmetic_without_executing_model_code(output):
    assert evaluate_micro_task(TASKS['code_fix'], output)


@pytest.mark.parametrize('output', [
    '# return a + b\ndef add(a, b):\n    return a - b',
    'def add(a, b):\n    return 5',
    'def add(a, b):\n    return __import__("os").system("echo unsafe")',
    'import os\ndef add(a, b):\n    return a + b',
    'def add(a: __import__("os"), b):\n    return a + b',
])
def test_code_probe_rejects_wrong_or_executable_payloads(output):
    assert not evaluate_micro_task(TASKS['code_fix'], output)


def test_plan_probe_requires_valid_schema_and_dependency():
    valid = {'steps': [{'id': 'inspect', 'title': 'Inspect', 'dependencies': []},
                       {'id': 'summarize', 'title': 'Summarize', 'dependencies': ['inspect']}]}
    assert evaluate_micro_task(TASKS['json_plan'], json.dumps(valid))
    assert not evaluate_micro_task(TASKS['json_plan'], '{ invalid steps')
    assert not evaluate_micro_task(TASKS['json_plan'], '{"steps": []}')
    valid['steps'][1]['dependencies'] = ['summarize']
    assert not evaluate_micro_task(TASKS['json_plan'], json.dumps(valid))


def test_reviewer_probe_identifies_the_actual_bug_and_fix():
    assert evaluate_micro_task(TASKS['summarize'], '{"bug_line":2,"fix":"return xs[0]"}')
    assert not evaluate_micro_task(TASKS['summarize'], 'build failed timeout')
    assert not evaluate_micro_task(TASKS['summarize'], '{"bug_line":1,"fix":"return xs[0]"}')
    assert not evaluate_micro_task(TASKS['summarize'], '{"bug_line":2,"fix":"return xs[1]"}')


def test_vision_and_latency_have_exact_known_answers():
    assert evaluate_micro_task(TASKS['vision'], 'white')
    assert evaluate_micro_task(TASKS['vision'], 'BEYAZ')
    assert not evaluate_micro_task(TASKS['vision'], 'black')
    assert not evaluate_micro_task(TASKS['vision'], 'white black blue green')
    assert evaluate_micro_task(TASKS['latency'], 'OK')
    assert not evaluate_micro_task(TASKS['latency'], 'not ok')


@pytest.mark.asyncio
async def test_output_is_validated_even_if_runner_claims_pass():
    async def runner(*_):
        return {'passed': True, 'output': '{ invalid steps', 'duration_seconds': 1}
    report = await calibrate_triple({'id': 'test', 'input_modalities': ['text']}, runner,
                                    tasks=[TASKS['json_plan']])
    assert report['results'][0]['passed'] is False


def test_invalid_utility_weights_are_not_used():
    for weight in [-1, 2, float('nan')]:
        assert role_weight({'role_weights': {'general': weight}, 'provenance': {'calibrated': True}}, 'general') is None
