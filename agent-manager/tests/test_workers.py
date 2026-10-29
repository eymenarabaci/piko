"""Specialist role contract: bounded delegation and real local media output."""
import shutil
import pytest

from manager.cao import CAOClient, CAOError
from manager.worker_catalog import (
    MAX_WORKER_CALLS, WorkerRequestError, extract_worker_calls,
    list_workers, worker_request_instructions,
)


def test_catalog_exposes_real_capabilities_and_local_media():
    workers = list_workers()
    assert 10 <= len(workers) <= 20
    assert len([w for w in workers if w["available"]]) >= 15
    roles = {w["role"]: w for w in workers}
    assert roles["visualizer"]["output_kind"] == "diagram_text"
    assert roles["image_brief_designer"]["available"]
    assert roles["image_generator"]["available"]
    assert roles["image_generator"]["output_kind"] == "image"
    assert roles["video_generator"]["available"] == bool(shutil.which("ffmpeg"))
    assert "<cao_workers>" in worker_request_instructions()


def test_worker_request_protocol_is_explicit_and_bounded():
    assert extract_worker_calls("Normal yanıt.") == []
    calls = extract_worker_calls(
        '<cao_workers>{"calls":[{"role":"planner","task":"İşi böl"}]}</cao_workers>'
    )
    assert [(call.role, call.task) for call in calls] == [("planner", "İşi böl")]
    assert extract_worker_calls('<cao_workers>{"calls":[{"role":"planner","task":"x"}]}</cao_workers>', depth=1) == []
    too_many = [{"role": "planner", "task": "x"}] * (MAX_WORKER_CALLS + 1)
    import json
    with pytest.raises(WorkerRequestError, match="Bir seferde"):
        extract_worker_calls("<cao_workers>" + json.dumps({"calls": too_many}) + "</cao_workers>")
    media_calls = extract_worker_calls('<cao_workers>{"calls":[{"role":"image_generator","task":"Vektör görsel oluştur"}]}</cao_workers>')
    assert media_calls[0].role == "image_generator"
    with pytest.raises(WorkerRequestError, match="tek"):
        extract_worker_calls('Açıklama <cao_workers>{"calls":[{"role":"planner","task":"x"}]}</cao_workers>')


@pytest.mark.asyncio
async def test_cao_worker_uses_existing_provider_and_cannot_escalate(tmp_path):
    client = CAOClient(tmp_path, tmp_path)
    calls, events = [], []

    async def run(model, prompt, *, workspace, allow_writes, timeout, on_event, persona):
        calls.append((model, prompt, workspace, allow_writes, timeout, persona))
        return {"output": "Denetim sonucu", "model_id": model["id"], "status": "completed"}

    async def event(kind, message, data):
        events.append((kind, data))

    client.run = run
    model = {"id": "gemini-test", "provider": "antigravity"}
    result = await client.run_worker("code_reviewer", "Kodu incele", model,
                                     workspace=str(tmp_path), allow_writes=True,
                                     timeout=600, on_event=event)
    assert calls[0][3:] == (False, 300, "reviewer")
    assert "Salt okunur" in calls[0][1]
    assert result["worker_role"] == "code_reviewer"
    assert [row[0] for row in events] == ["cao_worker_started", "cao_worker_completed"]
    with pytest.raises(CAOError, match="başka işçi"):
        await client.run_worker("planner", "Planla", model, workspace=str(tmp_path),
                                allow_writes=False, timeout=30, on_event=event, depth=1)


@pytest.mark.asyncio
async def test_cao_worker_requires_capable_writer_and_rejects_nested_result(tmp_path):
    client = CAOClient(tmp_path, tmp_path)
    events = []

    async def event(kind, message, data):
        events.append(kind)

    with pytest.raises(CAOError, match="dosya düzenleyemez"):
        await client.run_worker("code_writer", "Dosya değiştir", {"id": "qwen", "provider": "ollama"},
                                workspace=str(tmp_path), allow_writes=True, timeout=30, on_event=event)

    async def run(*_, **__):
        return {"output": '<cao_workers>{"calls":[]}</cao_workers>', "model_id": "codex"}

    client.run = run
    with pytest.raises(CAOError, match="iç içe çağrı"):
        await client.run_worker("planner", "Planla", {"id": "codex", "provider": "codex"},
                                workspace=str(tmp_path), allow_writes=False, timeout=30, on_event=event)
    assert events == ["cao_worker_started", "cao_worker_failed"]


@pytest.mark.asyncio
async def test_cao_media_worker_renders_svg_only_with_write_permission(tmp_path):
    client = CAOClient(tmp_path, tmp_path)
    async def event(*_): pass
    model = {"id": "qwen", "provider": "ollama"}
    with pytest.raises(CAOError, match="dosya yazma izni"):
        await client.run_worker("image_generator", "Mavi kare", model,
                                workspace=str(tmp_path), allow_writes=False, timeout=30, on_event=event)
    async def run(model, prompt, **kwargs):
        assert kwargs["allow_writes"] is False
        return {"output": '{"width":320,"height":180,"background":"#101827","shapes":[{"kind":"rectangle","x":0.1,"y":0.1,"width":0.3,"height":0.3,"color":"#3366ff"}]}',
                "model_id": model["id"], "status": "completed"}
    client.run = run
    result = await client.run_worker("image_generator", "Mavi kare", model,
                                     workspace=str(tmp_path), allow_writes=True, timeout=30, on_event=event)
    assert result["artifact"]["format"] == "svg"
    assert result["artifact"]["path"].endswith(".svg")
