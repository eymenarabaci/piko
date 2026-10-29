import json

import httpx
import pytest

from manager.ao import AOClient, AOError


@pytest.fixture
def client(tmp_path):
    run_file = tmp_path / "running.json"
    run_file.write_text(json.dumps({"port": 3001, "pid": 42}), encoding="utf-8")
    return run_file


@pytest.mark.asyncio
async def test_ao_codex_session_finishes_with_recorded_turn(client):
    calls = []
    polls = 0

    def handler(request):
        nonlocal polls
        path = request.url.path
        if path == "/readyz":
            return httpx.Response(200, json={"service": "agent-orchestrator-daemon", "pid": 42, "status": "ready"})
        if path == "/api/v1/sessions" and request.method == "POST":
            body = json.loads(request.content)
            assert body["mode"] == "chat"
            assert "model" not in body  # Generic Codex identity uses the AO account default.
            return httpx.Response(201, json={"session": {"id": "ao-1"}})
        if path == "/api/v1/sessions/ao-1":
            polls += 1
            return httpx.Response(200, json={"session": {"id": "ao-1", "activity": {"state": "idle" if polls > 1 else "active"}}})
        if path == "/api/v1/sessions/ao-1/history":
            return httpx.Response(200, json={"turns": [{"userRequest": "Agent Manager görev kimliği: am-1-task", "agentResponse": "İş tamamlandı."}], "total": 1})
        raise AssertionError(path)

    ao = AOClient(client, httpx.MockTransport(handler))
    async def project(_): return "project-1"
    async def spawned(sid): calls.append(sid)
    async def event(*_): pass
    ao.ensure_project = project
    result = await ao.run({"id": "codex", "provider": "codex", "base_model": "gpt-6.1-sol"},
                          "İşi yap", workspace="C:/project", allow_writes=True, timeout=10,
                          request_id="am-1-task", session_id=None, on_spawn=spawned, on_event=event)
    assert calls == ["ao-1"]
    assert result["output"] == "İş tamamlandı."
    assert result["ao_session_id"] == "ao-1"


@pytest.mark.asyncio
async def test_ao_agy_rejected_before_spawning(client):
    ao = AOClient(client, httpx.MockTransport(lambda _: pytest.fail("Unexpected AO request")))
    async def event(*_): pass
    with pytest.raises(AOError, match="doğrudan CLI"):
        await ao.run({"id": "gemini-3.8-flash-medium", "provider": "antigravity"},
                     "İşi yap", workspace="C:/project", allow_writes=True, timeout=10,
                     request_id="am-1-agy", session_id=None, on_spawn=event, on_event=event)


@pytest.mark.asyncio
async def test_ao_rejects_wrong_daemon_identity(client):
    ao = AOClient(client, httpx.MockTransport(lambda _: httpx.Response(200, json={"service": "other", "pid": 42, "status": "ready"})))
    with pytest.raises(AOError, match="kimliği"):
        await ao.request("GET", "/api/v1/projects")
