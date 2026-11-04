import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from manager.engine import Engine

def test_workspace_mode_follows_real_run_write_authority(tmp_path):
    async def scenario():
        engine=Engine.__new__(Engine)
        engine.default_workspace=tmp_path
        engine.resource_lock_timeout=0
        engine.resource_lock_ttl=60
        engine.resource_locks=SimpleNamespace(acquire=AsyncMock(return_value="lease"))
        for authority, expected in [(False,True),(True,False),(None,False)]:
            run={"id":"r","workspace":str(tmp_path)}
            if authority is not None:run["allow_writes"]=authority
            assert await engine._acquire_run_resources(run)=="lease"
            assert engine.resource_locks.acquire.call_args.kwargs["shared"] is expected
    asyncio.run(scenario())
