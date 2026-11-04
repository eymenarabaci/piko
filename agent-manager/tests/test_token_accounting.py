import asyncio
import json
import sqlite3
import sys
from unittest.mock import AsyncMock

import pytest

from manager.optimizer import TokenOptimizer
from manager.providers import Providers, ProviderError
from manager.processes import run_command, ProcessError
from manager.token_usage import opencode_usage
from manager.engine import Engine
from types import SimpleNamespace


def turn(part='one', message='m', incoming=5226):
    return json.dumps({'type': 'step_finish', 'part': {'id': part, 'messageID': message,
        'tokens': {'total': incoming + 86527, 'input': incoming, 'output': 89,
                   'reasoning': 38, 'cache': {'read': 86400, 'write': 0}}}})


def test_normalizes_real_opencode_schema_without_double_counting_reasoning_or_cache():
    usage = opencode_usage(turn())
    assert usage['input_tokens'] == 91626
    assert usage['output_tokens'] == 127
    assert usage['input_tokens'] + usage['output_tokens'] == 91753
    assert opencode_usage('{"type":"text","part":{"text":"secret"}}') is None
    assert opencode_usage(turn().replace('5226', '-1')) is None


def test_wrapper_counts_each_turn_once_and_marks_result_accounted(tmp_path):
    async def scenario():
        provider = Providers([{'id': 'm', 'provider': 'evren'}], tmp_path)
        events = []
        async def sink(kind, message, data=None):
            events.append((kind, data))
            return True
        async def fake(*args, on_event, **kwargs):
            await on_event('provider_started', '', {})
            for line in [turn(), turn(), turn('two', 'm2', 10)]:
                await on_event('provider_usage', '', opencode_usage(line))
            return {'model_id': 'm', 'output': 'ok', 'usage': {}}
        provider._run = fake
        result = await provider.run('m', 'prompt', workspace=str(tmp_path), allow_writes=False,
                                    timeout=5, on_event=sink)
        assert result['usage']['accounted'] is True
        assert result['usage']['input_tokens'] == 91626 + 86410
        assert len([e for e in events if e[0] == 'provider_usage']) == 2
    asyncio.run(scenario())


def test_timeout_preserves_completed_turn_usage_and_marks_remainder_unknown(tmp_path):
    async def scenario():
        provider = Providers([{'id': 'm', 'provider': 'evren'}], tmp_path)
        events = []
        async def sink(kind, message, data=None):
            events.append((kind, data))
            return True
        async def fake(*args, on_event, **kwargs):
            script = 'import time;print(' + repr(turn()) + ',flush=True);time.sleep(20)'
            return await run_command([sys.executable, '-c', script], root=tmp_path,
                workspace=tmp_path, timeout=5, on_event=on_event, live_format='opencode_jsonl')
        provider._run = fake
        with pytest.raises(ProcessError):
            await provider.run('m', 'prompt', workspace=str(tmp_path), allow_writes=False,
                               timeout=5, on_event=sink)
        usages = [data for kind, data in events if kind == 'provider_usage']
        assert [u['source'] for u in usages] == ['provider_reported', 'unknown']
        assert usages[0]['input_tokens'] == 91626
    asyncio.run(scenario())


def test_legacy_ledger_migration_dedup_and_breakdown(tmp_path):
    db = tmp_path / 'tokens.db'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE token_ledger(id INTEGER PRIMARY KEY,run_id TEXT,step_id TEXT,model_id TEXT,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,timestamp TEXT)')
        connection.execute("INSERT INTO token_ledger VALUES(1,'r','s','m',10,2,12,'old')")
    async def scenario():
        optimizer = TokenOptimizer(db)
        for _ in range(2):
            await optimizer.record_usage('r', 'planner', 'm', 100, 20,
                                         source='provider_reported', call_id='turn-one')
        await optimizer.record_usage('r', 's', 'm', 3, 2)
        await optimizer.record_usage('r', 's', 'm', 0, 0, source='unknown', call_id='failed')
        report = await optimizer.get_run_breakdown('r')
        assert report['total_tokens'] == 125
        assert report['provider_reported_tokens'] == 125
        assert 'estimated_tokens' not in report
        assert 'legacy_unclassified_tokens' not in report
        assert report['unmeasured_records'] == 2
        assert report['unknown_calls'] == 1
        assert not report['is_complete']
    asyncio.run(scenario())


def test_missing_usage_is_unknown_and_never_estimated(tmp_path):
    async def scenario():
        provider = Providers([{'id': 'm', 'provider': 'evren'}], tmp_path)
        provider._run = AsyncMock(return_value={'output': 'answer', 'model_id': 'm'})
        events = []
        async def sink(kind, message, data):
            events.append(data)
            return True
        result = await provider.run('m', 'prompt', workspace=str(tmp_path), allow_writes=False,
                                    timeout=5, on_event=sink)
        assert result['usage']['source'] == 'unknown'
        assert events[-1]['source'] == 'unknown'
        assert result['usage']['input_tokens'] == result['usage']['output_tokens'] == 0
    asyncio.run(scenario())


def test_stream_accounting_not_reinserted_on_completion_and_source_is_preserved(tmp_path):
    async def scenario():
        engine = Engine.__new__(Engine)
        engine.optimizer = TokenOptimizer(tmp_path / 'usage.db')
        engine.store = SimpleNamespace(event=AsyncMock())
        emit = engine.emitter('run', 'planner')
        usage = {'input_tokens': 100, 'output_tokens': 20, 'source': 'provider_reported',
                 'call_id': 'unique-turn', 'model_id': 'evren-test'}
        assert await emit('provider_usage', 'usage', usage) is True
        await emit('provider_usage', 'duplicate', usage)
        result = {'model_id': 'evren-test', 'output': 'answer',
                  'usage': {**usage, 'accounted': True}}
        stats = await engine.record_result_usage('run', 'planner', result, 'prompt')
        assert stats['step_tokens'] == stats['run_total_tokens'] == 120
        report = await engine.optimizer.get_run_breakdown('run')
        assert report['provider_reported_tokens'] == 120
        assert report['unmeasured_records'] == 0
    asyncio.run(scenario())


def test_evren_cli_has_no_forced_wall_clock_limit(tmp_path):
    async def scenario():
        provider = Providers([{'id':'m','provider':'evren'}],tmp_path)
        provider._run=AsyncMock(return_value={'model_id':'m','output':'done'})
        async def sink(*args): return True
        await provider.run('m','task',workspace=str(tmp_path),allow_writes=False,timeout=1200,on_event=sink)
        assert provider._run.call_args.kwargs['timeout']==0
    asyncio.run(scenario())
