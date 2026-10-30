import asyncio
from pathlib import Path
from types import SimpleNamespace

from manager.engine import Engine
from manager.graphify_context import GraphifyContext


def test_graphify_adds_bounded_code_context_and_reuses_index(tmp_path):
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    calls = []

    async def fake_runner(command, **options):
        calls.append((command, options))
        if command[1] == 'extract':
            graph = Path(command[-1]) / 'graphify-out' / 'graph.json'
            graph.parent.mkdir(parents=True, exist_ok=True)
            graph.write_text('{"nodes":[]}', encoding='utf-8')
            return {'returncode': 0, 'stdout': '', 'stderr': ''}
        return {'returncode': 0, 'stdout': 'auth.py -> api.py\n', 'stderr': ''}

    adapter = GraphifyContext(tmp_path / 'data', cli='graphify', runner=fake_runner)

    async def check():
        assert await adapter.context(workspace, 'auth', max_chars=10) == 'auth.py ->'
        assert await adapter.context(workspace, 'auth') == 'auth.py -> api.py'
        adapter.invalidate(workspace)
        assert await adapter.context(workspace, 'auth') == 'auth.py -> api.py'

    asyncio.run(check())
    assert [command[1] for command, _ in calls] == ['extract', 'query', 'extract', 'query']
    assert '--code-only' in calls[0][0]
    assert calls[0][1]['env']['GRAPHIFY_BACKEND'] == 'none'
    assert calls[0][1]['env']['GRAPHIFY_QUERY_LOG_DISABLE'] == '1'


def test_graphify_failure_falls_back_without_query(tmp_path):
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    calls = []

    async def fake_runner(command, **_options):
        calls.append(command)
        return {'returncode': 1, 'stdout': '', 'stderr': 'failed'}

    adapter = GraphifyContext(tmp_path / 'data', cli='graphify', runner=fake_runner)

    async def check():
        assert await adapter.context(workspace, 'auth') == ''
        assert await adapter.context(workspace, 'auth') == ''

    asyncio.run(check())
    assert len(calls) == 1


def test_engine_keeps_project_memory_with_graphify_context(tmp_path):
    engine = object.__new__(Engine)
    engine.project_memory_locks = {}
    engine.project_memory = SimpleNamespace(context=lambda *_args, max_chars: 'kalıcı proje kuralı'[:max_chars])

    async def graph_context(*_args, max_chars):
        return 'auth.py çağrıları'[:max_chars]

    engine.graphify_context = SimpleNamespace(context=graph_context)
    text = asyncio.run(engine.project_memory_context(tmp_path, 'auth', max_chars=120))
    assert 'Graphify kod ilişkileri' in text
    assert 'auth.py çağrıları' in text
    assert 'kalıcı proje kuralı' in text
    assert len(text) <= 120
