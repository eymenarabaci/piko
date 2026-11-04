import asyncio
import sys
import pytest
from pathlib import Path
from test_engine import FakeProviders,engine_at,request,settle,step
from manager.schema import Plan


@pytest.mark.parametrize("width", [2, 6])
async def test_real_subprocess_writers_overlap_and_merge(tmp_path,width):
    names=["left","right","third","fourth","fifth","sixth"][:width]
    workspace=tmp_path/"source";workspace.mkdir();(workspace/"base.txt").write_text("base")
    signals=tmp_path/"signals";signals.mkdir()
    paths=[];children=[]
    (tmp_path/"engine").mkdir()
    async with engine_at(tmp_path/"engine",FakeProviders()) as engine:
        created=await engine.create(request(mode="plan",allow_writes=True,workspace=str(workspace),parallel_writes=True,max_parallel=width))
        run=await settle(engine,created["id"])
        await engine.edit(run["id"],Plan(steps=[step(name) for name in names]))
        run=await engine.store.get(run["id"])
        async def execute(local_run, current, prompt, **kwargs):
            paths.append(local_run["workspace"])
            code="""import pathlib,sys,time
signals=pathlib.Path(sys.argv[1]);name=sys.argv[2]
(signals/(name+'.started')).write_text('started')
end=time.monotonic()+8
while len(list(signals.glob('*.started')))<int(sys.argv[3]):
 if time.monotonic()>end:raise RuntimeError('writers did not overlap')
 time.sleep(.02)
pathlib.Path(name+'.txt').write_text(name)
"""
            proc=await asyncio.create_subprocess_exec(sys.executable,"-B","-c",code,str(signals),current.id,str(width),cwd=local_run["workspace"],stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            children.append(proc)
            _,err=await proc.communicate()
            assert proc.returncode==0,err.decode()
            return {"model_id":"worker","output":"implemented "+current.id,"usage":{}}
        async def review(local_run,current,result):
            assert (Path(local_run["workspace"])/(current.id+'.txt')).exists()
            return result,{"status":"pass","test_result":"fixture independent inspection"}
        engine.run_executor=execute;engine.verify_implementation=review
        try:
            graph=await engine.execution_graph(run)
            result=await graph["ready_dispatch"]({"results":{}})
            assert set(result["results"])==set(names)
            assert len(set(paths))==width and str(workspace) not in paths
            assert (workspace/"left.txt").read_text()=="left"
            assert (workspace/"right.txt").read_text()=="right"
            assert (workspace/"base.txt").read_text()=="base"
            assert all(not Path(p).exists() for p in paths)
        finally:
            for child in children:
                if child.returncode is None:child.kill()
            await asyncio.gather(*(child.wait() for child in children))


async def test_conflicting_isolated_writers_never_silently_overwrite(tmp_path,monkeypatch):
    import pytest
    workspace=tmp_path/"source";workspace.mkdir();(workspace/"shared.txt").write_text("base")
    (tmp_path/"engine").mkdir()
    monkeypatch.setattr("manager.engine.tempfile.gettempdir",lambda:str(tmp_path))
    barrier=asyncio.Event();active=0;paths=[]
    async with engine_at(tmp_path/"engine",FakeProviders()) as engine:
        created=await engine.create(request(mode="plan",allow_writes=True,workspace=str(workspace),parallel_writes=True,max_parallel=2))
        run=await settle(engine,created["id"])
        await engine.edit(run["id"],Plan(steps=[step("left"),step("right")]))
        run=await engine.store.get(run["id"])
        async def execute(local_run,current,prompt,**kwargs):
            nonlocal active
            paths.append(Path(local_run["workspace"]))
            (Path(local_run["workspace"])/"shared.txt").write_text(current.id)
            active+=1
            if active==2:barrier.set()
            await asyncio.wait_for(barrier.wait(),3)
            return {"model_id":"worker","output":"implemented","usage":{}}
        async def review(local_run,current,result):return result,{"status":"pass"}
        engine.run_executor=execute;engine.verify_implementation=review
        graph=await engine.execution_graph(run)
        with pytest.raises(Exception,match="conflicts"):
            await graph["ready_dispatch"]({"results":{}})
        saved=await engine.store.get(run["id"])
        assert sorted(x["status"] for x in saved["results"].values())==["completed","failed"]
        assert (workspace/"shared.txt").read_text() in {"left","right"}
        assert sum(p.exists() for p in paths)==1  # conflicting evidence retained
