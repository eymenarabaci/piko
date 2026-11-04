import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from manager.engine import Engine
from manager.providers import Providers,ProviderError

def test_network_failure_resumes_persisted_same_step_and_model_session(tmp_path):
    async def scenario():
        run={'id':'r','execution_id':'phase-one','cli_sessions':{}}
        async def get(_): return json.loads(json.dumps(run))
        async def save(value): run.clear();run.update(json.loads(json.dumps(value)))
        engine=Engine.__new__(Engine);engine.locks={};engine.optimizer=SimpleNamespace(record_usage=AsyncMock())
        engine.store=SimpleNamespace(get=get,save=save,event=AsyncMock())
        provider=Providers([{'id':'evren','provider':'evren'}],tmp_path)
        seen=[]
        async def call(*args,on_event,session_id=None,**kwargs):
            seen.append(session_id)
            if len(seen)==1:
                assert session_id.startswith('ses_')
                raise ProviderError('network transport')
            return {'model_id':'evren','output':'done'}
        provider._run=call
        emit=engine.emitter('r','step3')
        with pytest.raises(ProviderError):
            await provider.run('evren','original task',workspace=str(tmp_path),allow_writes=True,timeout=1200,on_event=emit)
        await provider.run('evren','continue',workspace=str(tmp_path),allow_writes=True,timeout=1200,on_event=emit)
        assert seen[0] == seen[1] and seen[0].startswith('ses_')
        assert await engine.emitter('r','other-step')('provider_session_lookup','',{'model_id':'evren'}) is None
        assert await emit('provider_session_lookup','',{'model_id':'other-model'}) is None
        run['execution_id']='next-phase'
        assert await emit('provider_session_lookup','',{'model_id':'evren'}) is None
    asyncio.run(scenario())


def test_legacy_network_wait_migrates_without_touching_retry_deadline():
    async def scenario():
        run={'id':'r','status':'waiting_quota','phase_status':'retrying','quota_timer':{'resets_at':'2099-01-01T00:00:00+00:00','execute':True}}
        engine=Engine.__new__(Engine)
        engine.store=SimpleNamespace(list=AsyncMock(return_value=[run]),list_messages=AsyncMock(return_value=[]),save=AsyncMock())
        from unittest.mock import Mock
        engine.schedule_quota_resume=Mock()
        await engine.recover()
        assert run['status']=='waiting_retry' and run['quota_timer'] is None
        assert run['retry_timer']['resets_at']=='2099-01-01T00:00:00+00:00'
        engine.schedule_quota_resume.assert_called_once_with('r',run['retry_timer'])
    asyncio.run(scenario())
