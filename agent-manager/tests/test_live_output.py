import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from manager import live_output
from manager.processes import run_command
from manager.store import Store


def test_output_is_bounded_redacted_and_excludes_reasoning():
    live_output.start('safe-output', 1)
    live_output.append_event('safe-output', json.dumps({'type':'reasoning','part':{'text':'hidden reasoning'}}))
    live_output.append_event('safe-output', json.dumps({'type':'tool_use','part':{'tool':'read','state':{'status':'completed','title':'app.py','output':'private raw payload'}}}))
    live_output.append_event('safe-output', json.dumps({'type':'text','part':{'text':'Visible API_KEY=do-not-show'}}))
    text=live_output.snapshot('safe-output')['output']
    assert 'Visible' in text and 'do-not-show' not in text
    assert 'hidden reasoning' not in text and 'private raw payload' not in text
    live_output.append('safe-output', 'x' * 100000)
    assert len(live_output.snapshot('safe-output')['output']) <= live_output.LIMIT
    live_output.finish('safe-output','completed')


@pytest.mark.asyncio
async def test_real_subprocess_output_is_visible_before_exit(tmp_path):
    started=asyncio.Event(); target={}
    async def event(kind,message,data):
        if kind=='provider_started':
            target['id']=data['live_output_id'];started.set()
    task=asyncio.create_task(run_command([sys.executable,'-u','-c','import time; print("first",flush=True); time.sleep(1); print("last",flush=True)'],root=tmp_path,workspace=tmp_path,timeout=5,on_event=event))
    try:
        await asyncio.wait_for(started.wait(),3)
        async with asyncio.timeout(3):
            while 'first' not in live_output.snapshot(target['id'])['output']:
                await asyncio.sleep(.01)
        assert not task.done()
        assert live_output.snapshot(target['id'])['state']=='running'
        await task
        snapshot=live_output.snapshot(target['id'])
        assert snapshot['state']=='completed' and 'last' in snapshot['output']
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_output_metadata_scoped_to_run_step_and_review(tmp_path):
    store=Store(tmp_path/'runs.sqlite');await store.open()
    try:
        await store.event('other-run','provider_started','start',{'step_id':'adim_1','live_output_id':'foreign'})
        await store.event('run','provider_started','start',{'step_id':'adimX1','live_output_id':'wrong-step'})
        assert not (await store.latest_provider_output('run','adim_1'))['provider_started']
        await store.event('run','provider_started','start',{'step_id':'adim_1:review','live_output_id':'owned'})
        assert (await store.latest_provider_output('run','adim_1'))['provider_started']['live_output_id']=='owned'
        await store.event('run','step_started','restarted',{'step_id':'adim_1'})
        metadata=await store.latest_provider_output('run','adim_1')
        assert metadata['step_started']['_event_id'] > metadata['provider_started']['_event_id']
    finally:
        await store.close()


def test_readonly_api_does_not_expose_another_runs_output(tmp_path,monkeypatch):
    import manager.app as app_module
    async def no_discovery(_):return []
    monkeypatch.setattr(app_module,'load_and_discover_models',no_discovery)
    db=sqlite3.connect(tmp_path/'runs.sqlite');db.execute('create table runs(id text primary key,payload text not null)')
    for rid in ['one','two']:
        db.execute('insert into runs values(?,?)',(rid,json.dumps({'id':rid,'status':'completed','results':{},'plan':None,'current_steps':[],'workspace':str(tmp_path)})))
    db.commit();db.close()
    providers=SimpleNamespace(models=[],available=lambda:[])
    with TestClient(app_module.create_app(data_dir=tmp_path,providers=providers)) as client:
        db=sqlite3.connect(tmp_path/'runs.sqlite')
        db.execute('insert into events(run_id,kind,message,created_at,data) values(?,?,?,?,?)',('one','provider_started','start','now',json.dumps({'step_id':'adim_1','live_output_id':'api-owned'})))
        db.commit();db.close()
        live_output.start('api-owned',123);live_output.append('api-owned','owned output')
        response=client.get('/api/runs/one/steps/adim_1/live-output')
        assert response.status_code==200 and response.json()['output']=='owned output'
        assert response.json()['read_only'] is True
        assert 'owned output' not in client.get('/api/runs/two/steps/adim_1/live-output').json()['output']
        assert client.get('/api/runs/missing/steps/adim_1/live-output').status_code==404
        live_output.finish('api-owned','completed')
