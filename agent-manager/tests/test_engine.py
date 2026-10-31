"""Integration tests use the real upstream graph/checkpointer, not a fake scheduler."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import json
import re

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError
import pytest

from manager.app import create_app
from manager.engine import Engine
from manager.adaptive import AdaptiveDecision
from manager.program import PhaseAudit
from manager.schema import Plan, RunRequest, parse_plan, validate_models, workspace_path
from manager.store import Store


def step(sid, dependencies=(), model="worker"):
    return {"id": sid, "title": sid, "prompt": f"TASK_{sid}",
            "model_id": model, "dependencies": list(dependencies)}


class FakeProviders:
    models = [
        {"id": "planner", "provider": "test", "execution_supported": True},
        {"id": "worker", "provider": "test", "execution_supported": True},
        {"id": "disabled", "provider": "test", "execution_supported": False},
    ]

    def __init__(self, steps=None):
        self.steps = steps or [step("first")]
        self.calls = []
        self.fail_once = set()
        self.counts = Counter()
        self.started = asyncio.Event()
        self.block = False
        self.active = 0
        self.max_active = 0
        self.parallel_barrier = None

    def available(self):
        return [{"id": provider, "available": True, "executable": "test-only"}
                for provider in {model["provider"] for model in self.models}]

    async def run(self, model_id, prompt, *, workspace, allow_writes, timeout, on_event, effort=""):
        self.calls.append({"model": model_id, "prompt": prompt, "writes": allow_writes})
        if "Sen iş ayrıştırıcısısın" in prompt:
            return {"output": json.dumps({"items": [{"title": "Görev", "goal": "Bir görev"}]}),
                    "model_id": model_id, "status": "completed", "usage": {}}
        if "Sen bağımsız ve salt okunur iş denetçisisin" in prompt:
            return {"output": json.dumps({"action": "accept", "reason": "Hedef karşılandı."}),
                    "model_id": model_id, "status": "completed", "usage": {}}
        if model_id == "planner":
            assert not allow_writes, "Planning must never inherit write permission"
            return {"output": json.dumps({"steps": self.steps}), "model_id": model_id,
                    "status": "completed", "usage": {}}
        match = re.search(r"=== ŞU AN YÜRÜTÜLEN ADIM: [^\n]+ ===\nTASK_([a-z_]+)", prompt)
        assert match or "=== ŞU AN YÜRÜTÜLEN ADIM: Doğrudan yanıt ===" in prompt, "Adım istemi beklenen görev işaretini içermiyor."
        sid = match.group(1) if match else "response"
        self.counts[sid] += 1
        self.started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.parallel_barrier is not None:
                if self.active == 2:
                    self.parallel_barrier.set()
                await asyncio.wait_for(self.parallel_barrier.wait(), 2)
            await on_event("progress", f"Started {sid}", {"test": True})
            if self.block:
                await asyncio.Event().wait()
            await asyncio.sleep(0.03)
            if sid in self.fail_once:
                self.fail_once.remove(sid)
                # Let a successful sibling commit its result before this deliberate failure.
                await asyncio.sleep(0.3)
                raise RuntimeError("Deliberate provider failure")
            return {"output": f"RESULT_{sid}", "model_id": model_id,
                    "status": "completed", "usage": {"output_tokens": 2}}
        finally:
            self.active -= 1


@asynccontextmanager
async def engine_at(path, provider):
    store = Store(path / "runs.sqlite")
    await store.open()
    try:
        async with AsyncSqliteSaver.from_conn_string(str(path / "checkpoints.sqlite")) as saver:
            engine = Engine(store, saver, provider, path)
            await engine.recover()
            try:
                yield engine
            finally:
                await engine.close()
    finally:
        await store.close()


async def settle(engine, run_id):
    task = engine.tasks.get(run_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 5)
    return await engine.store.get(run_id)


def request(**changes):
    values = {"prompt": "Bir görev", "planner_model": "planner", "executor_model": "worker"}
    return RunRequest(**(values | changes))


@pytest.mark.asyncio
async def test_plan_edit_resume_dependency_results_and_events(tmp_path):
    provider = FakeProviders()
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="plan", allow_writes=True))
        run = await settle(engine, created["id"])
        assert run["status"] == "planned"
        assert [c["model"] for c in provider.calls] == ["planner", "planner"]
        edited = Plan(steps=[step("prepare"), step("review", ["prepare"])])
        await engine.edit(run["id"], edited)
        await engine.resume(run["id"])
        result = await settle(engine, run["id"])
        assert result["status"] == "completed", result.get("error")
        assert set(result["results"]) == {"prepare", "review"}
        assert result["results"]["review"]["output"] == "RESULT_review"
        review_call = next(c for c in provider.calls if "TASK_review" in c["prompt"])
        assert "RESULT_prepare" in review_call["prompt"]
        assert result["current_steps"] == []
        event_page = await engine.store.events(run["id"])
        kinds = [event["kind"] for event in event_page["events"]]
        assert kinds.count("step_completed") == 2
        assert "completed" in kinds
        assert (await engine.store.events(run["id"], event_page["cursor"]))["events"] == []
        with pytest.raises(ValueError):
            await engine.edit(run["id"], edited)


@pytest.mark.asyncio
async def test_resume_after_reopen_preserves_successful_sibling(tmp_path):
    provider = FakeProviders([step("good"), step("flaky"), step("joined", ["good", "flaky"])])
    provider.fail_once.add("flaky")
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="orchestrate"))
        failed = await settle(engine, created["id"])
        assert failed["status"] == "failed", failed.get("error")
        assert failed["results"]["good"]["status"] == "completed"
        assert provider.counts == {"good": 1, "flaky": 1}
        # Emulate the run index left active at process loss; checkpoint files remain on disk.
        await engine.change(created["id"], status="running", current_steps=["flaky"])
    async with engine_at(tmp_path, provider) as engine:
        interrupted = await engine.store.get(created["id"])
        assert interrupted["status"] == "interrupted"
        assert interrupted["current_steps"] == []
        assert provider.counts == {"good": 1, "flaky": 1}, "Recovery must not run CLIs automatically"
        await engine.resume(created["id"])
        completed = await settle(engine, created["id"])
        assert completed["status"] == "completed", completed.get("error")
        assert provider.counts == {"good": 1, "flaky": 2, "joined": 1}
        joined = next(c for c in provider.calls if "TASK_joined" in c["prompt"])
        assert "RESULT_good" in joined["prompt"] and "RESULT_flaky" in joined["prompt"]
        assert len([c for c in provider.calls if c["model"] == "planner" and "Sen görev planlayıcısısın" in c["prompt"]]) == 1


@pytest.mark.asyncio
async def test_cancel_active_node_clears_running_state(tmp_path):
    provider = FakeProviders()
    provider.block = True
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="orchestrate"))
        await asyncio.wait_for(provider.started.wait(), 5)
        cancelled = await asyncio.wait_for(engine.cancel(created["id"]), 5)
        assert cancelled["status"] == "cancelled"
        assert cancelled["current_steps"] == []
        assert provider.active == 0
        assert not any(r.get("status") == "completed" for r in cancelled["results"].values())


@pytest.mark.asyncio
async def test_cancel_before_driver_first_turn_is_persisted(tmp_path):
    provider = FakeProviders()
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="orchestrate"))
        # No event-loop yield after create: the background driver has not started.
        cancelled = await engine.cancel(created["id"])
        assert cancelled["status"] == "cancelled"
        assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_writes,expected", [(False, 2), (True, 1)])
async def test_shared_writable_workspace_serializes_workers(tmp_path, allow_writes, expected):
    provider = FakeProviders([step("left"), step("right")])
    if not allow_writes:
        provider.parallel_barrier = asyncio.Event()
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="orchestrate", allow_writes=allow_writes, max_parallel=2))
        run = await settle(engine, created["id"])
        assert run["status"] == "completed", run.get("error")
        assert provider.max_active == expected


@pytest.mark.asyncio
async def test_ao_backend_uses_langgraph_and_awaits_worktree_review(tmp_path):
    provider = FakeProviders()
    provider.models.append({"id": "codex", "provider": "codex", "execution_supported": True})

    class FakeAO:
        async def health(self): return {"available": True}
        async def run(self, model, prompt, *, on_spawn, on_event, **kwargs):
            assert model["id"] == "codex"
            assert "=== ŞU AN YÜRÜTÜLEN ADIM:" in prompt
            await on_spawn("ao-test-1")
            return {"output": "AO sonucu", "ao_session_id": "ao-test-1", "status": "completed"}

    async with engine_at(tmp_path, provider) as engine:
        engine.ao = FakeAO()
        created = await engine.create(request(mode="chat", executor_model="codex",
                                              execution_backend="ao", allow_writes=True))
        result = await settle(engine, created["id"])
        assert result["status"] == "awaiting_review", result.get("error")
        assert result["results"]["response"]["ao_session_id"] == "ao-test-1"
        assert all(call["model"] != "codex" for call in provider.calls)


@pytest.mark.parametrize("steps", [
    [step("a", ["b"]), step("b", ["a"])],
    [step("a", ["missing"])],
    [step("a"), step("a")],
    [step("a", ["a"])],
    [step("a"), step("b", ["a", "a"])],
])
def test_invalid_dependency_graphs_rejected(steps):
    with pytest.raises(ValidationError):
        Plan(steps=steps)


def test_plan_parse_model_and_workspace_validation(tmp_path):
    plan = parse_plan("```json\n" + json.dumps({"steps": [step("a")]}) + "\n```")
    assert plan.steps[0].id == "a"
    with pytest.raises(ValueError):
        parse_plan(json.dumps({"steps": [step("a")]}) + " extra text")
    with pytest.raises(ValueError):
        validate_models(Plan(steps=[step("a", model="missing")]), {m["id"]: m for m in FakeProviders.models})
    with pytest.raises(ValueError):
        validate_models(Plan(steps=[step("a", model="disabled")]), {m["id"]: m for m in FakeProviders.models})
    assert workspace_path("", tmp_path) == str(tmp_path.resolve())
    with pytest.raises(ValueError):
        workspace_path(str(tmp_path / "does-not-exist"), tmp_path)


@pytest.mark.asyncio
async def test_invalid_run_models_rejected_before_creation(tmp_path):
    async with engine_at(tmp_path, FakeProviders()) as engine:
        with pytest.raises(ValueError):
            await engine.create(request(executor_model="missing"))
        assert await engine.store.list() == []


def test_api_local_boundary_and_unknown_run(tmp_path):
    app = create_app(data_dir=tmp_path, providers=FakeProviders())
    with TestClient(app) as client:
        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["engine"] == "LangGraph"
        assert client.get("/api/runs/missing").status_code == 404
        assert client.post("/api/runs/missing/resume", json={}).status_code == 404
        assert client.get("/api/runs", headers={"Host": "attacker.example"}).status_code == 401
        assert client.post("/api/runs", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.post("/api/runs", content="not-json").status_code == 415
        assert client.post("/api/runs", json={}).status_code == 422
        assert client.post("/api/runs", json={"prompt": "x", "planner_model": "missing", "executor_model": "worker"}).status_code == 400


def routing_models():
    return [
        {"id": "planner", "provider": "test", "execution_supported": True},
        {"id": "codex_a", "provider": "codex", "execution_supported": True,
         "capabilities": ["general", "development", "reviewer", "master"],
         "priority": {"general": 1, "development": 1, "reviewer": 1, "master": 1}},
        {"id": "agy_b", "provider": "antigravity", "execution_supported": True,
         "capabilities": ["general", "development", "reviewer", "master"],
         "priority": {"general": 2, "development": 2, "reviewer": 2, "master": 2}},
    ]


@pytest.mark.asyncio
async def test_auto_route_hands_off_on_quota_error(tmp_path):
    class QuotaProviders(FakeProviders):
        models = routing_models()

        async def run(self, model_id, prompt, **kwargs):
            if model_id == "codex_a":
                self.calls.append({"model": model_id})
                raise RuntimeError("Sağlayıcı kullanım kotası doldu.")
            return await super().run(model_id, prompt, **kwargs)

    provider = QuotaProviders()
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="chat", executor_model="auto"))
        done = await settle(engine, created["id"])
        assert done["status"] == "completed", done.get("error")
        assert done["results"]["response"]["model_id"] == "agy_b"
        assert done["plan"]["steps"][0]["model_id"] == "agy_b"
        events = (await engine.store.events(done["id"]))["events"]
        assert any(event["kind"] == "model_handoff" for event in events)


@pytest.mark.asyncio
async def test_empty_planner_response_uses_only_ready_local_fallback(tmp_path):
    class EmptyPlanner(FakeProviders):
        models = [*FakeProviders.models,
                  {"id": "local-plan", "provider": "ollama", "execution_supported": True,
                   "capabilities": ["architect", "general"]}]

        async def run(self, model_id, prompt, **kwargs):
            if model_id == "planner":
                raise RuntimeError("Antigravity boş yanıt döndürdü.")
            if model_id == "local-plan":
                return {"output": json.dumps({"steps": [step("first")]}),
                        "model_id": model_id, "status": "completed", "usage": {}}
            return await super().run(model_id, prompt, **kwargs)

    async with engine_at(tmp_path, EmptyPlanner()) as engine:
        async def ready(): return {"local-plan": True, "ollama": True}
        engine.model_availability = ready
        created = await engine.create(request(mode="plan"))
        planned = await settle(engine, created["id"])
        assert planned["status"] == "planned", planned.get("error")
        assert planned["planner_model_actual"] == "local-plan"
        events = (await engine.store.events(planned["id"]))["events"]
        assert any(event["kind"] == "planner_local_fallback" for event in events)


@pytest.mark.asyncio
async def test_auto_orchestrator_repairs_in_new_plan_without_replaying_old_step(tmp_path):
    class RoutedProviders(FakeProviders):
        models = routing_models()

    provider = RoutedProviders([step("first", model="auto")])
    async with engine_at(tmp_path, provider) as engine:
        async def audit(run):
            if run["program_attempt"] == 0:
                provider.steps = [step("verify", model="auto")]
                return PhaseAudit(action="repair", reason="Doğrulama eksik.", repair_goal="Doğrula")
            return PhaseAudit(action="accept", reason="Doğrulandı.")
        engine.audit_phase = audit
        created = await engine.create(request(mode="orchestrate", executor_model="auto"))
        done = await settle(engine, created["id"])
        assert done["status"] == "completed", done.get("error")
        assert done["program_attempt"] == 1
        assert len(done["plan_history"]) == 2
        assert provider.counts == {"first": 1, "verify": 1}
        assert set(done["results"]) == {"verify"}


@pytest.mark.asyncio
async def test_writer_waits_for_independent_review_then_repairs_once(tmp_path):
    class ReviewProviders(FakeProviders):
        models = [*FakeProviders.models,
                  {"id": "checker", "provider": "codex", "execution_supported": True,
                   "capabilities": ["reviewer"], "priority": {"reviewer": 1}}]

        def __init__(self):
            super().__init__([step("first")])
            self.reviews = 0
            self.repairs = 0

        async def run(self, model_id, prompt, **kwargs):
            if "Sen bağımsız, salt okunur QA denetçisisin" in prompt:
                assert kwargs["allow_writes"] is False
                self.reviews += 1
                status = "fail" if self.reviews == 1 else "pass"
                findings = ([{"path": "module.py", "line": 3,
                              "issue": "Hedef yanlış", "evidence": "İlgili satır hatalı"}]
                            if status == "fail" else [])
                return {"output": json.dumps({"status": status, "findings": findings}),
                        "model_id": model_id, "status": "completed"}
            if "Sen aynı adımın hedefli düzeltme uygulayıcısısın" in prompt:
                self.repairs += 1
                assert kwargs["allow_writes"] is True
                return {"output": "DÜZELTİLDİ", "model_id": model_id, "status": "completed"}
            return await super().run(model_id, prompt, **kwargs)

    provider = ReviewProviders()
    async with engine_at(tmp_path, provider) as engine:
        created = await engine.create(request(mode="orchestrate", allow_writes=True))
        done = await settle(engine, created["id"])
        assert done["status"] == "completed", done.get("error")
        assert provider.reviews == 2 and provider.repairs == 1
        assert done["results"]["first"]["output"] == "DÜZELTİLDİ"
        assert done["results"]["first"]["independent_review"]["status"] == "pass"


@pytest.mark.asyncio
async def test_next_work_plan_is_created_only_after_previous_audit(tmp_path):
    class ProgramProviders(FakeProviders):
        async def run(self, model_id, prompt, **kwargs):
            if "Sen iş ayrıştırıcısısın" in prompt:
                return {"output": json.dumps({"items": [{"title": "Bir", "goal": "İlk iş"},
                                                          {"title": "İki", "goal": "İkinci iş"}]}),
                        "model_id": model_id, "status": "completed"}
            return await super().run(model_id, prompt, **kwargs)

    provider = ProgramProviders()
    async with engine_at(tmp_path, provider) as engine:
        audits = []
        async def audit(run):
            audits.append(run["program_index"])
            if run["program_index"] == 0:
                assert provider.counts == {"first": 1}
                provider.steps = [step("second")]
            return PhaseAudit(action="accept", reason="Dosya ve sonuç denetlendi.")
        engine.audit_phase = audit
        created = await engine.create(request(mode="orchestrate"))
        done = await settle(engine, created["id"])
        assert done["status"] == "completed", done.get("error")
        assert audits == [0, 1]
        assert provider.counts == {"first": 1, "second": 1}
        assert len(done["completed_items"]) == 2


@pytest.mark.asyncio
async def test_user_test_pauses_then_audits_without_replaying_implementation(tmp_path):
    provider = FakeProviders()
    async with engine_at(tmp_path, provider) as engine:
        async def audit(run):
            if not run.get("user_test_answer"):
                return PhaseAudit(action="await_user_test", reason="Fiziksel kontrol gerekli.",
                                  question="Cihazda düğme çalışıyor mu?")
            return PhaseAudit(action="accept", reason="Kullanıcı cihazda doğruladı.")
        engine.audit_phase = audit
        created = await engine.create(request(mode="orchestrate"))
        waiting = await settle(engine, created["id"])
        assert waiting["status"] == "awaiting_user_test"
        await engine.submit_user_test(created["id"], "Evet, cihazda çalıştı.")
        done = await settle(engine, created["id"])
        assert done["status"] == "completed", done.get("error")
        assert provider.counts == {"first": 1}


@pytest.mark.asyncio
async def test_cao_parent_can_call_one_bounded_worker(tmp_path):
    class RoutedProviders(FakeProviders):
        models = routing_models()

    class FakeCAO:
        def __init__(self): self.parent_calls = 0; self.worker_calls = []
        async def health(self): return {"available": True}
        async def close(self): pass
        async def clear_terminal_pool(self, workspace=None): pass
        async def run(self, model, prompt, **kwargs):
            self.parent_calls += 1
            output = ('<cao_workers>{"calls":[{"role":"technical_writer","task":"Kısa özet yaz"}]}</cao_workers>'
                      if self.parent_calls == 1 else 'Nihai yanıt')
            return {"output": output, "model_id": model["id"], "status": "completed"}
        async def run_worker(self, role, task, model, **kwargs):
            self.worker_calls.append((role, task, model["id"]))
            return {"output": "İşçi özeti", "model_id": model["id"], "status": "completed"}

    async with engine_at(tmp_path, RoutedProviders()) as engine:
        cao = FakeCAO(); engine.cao = cao
        created = await engine.create(request(mode="chat", executor_model="auto", execution_backend="cao"))
        done = await settle(engine, created["id"])
        assert done["status"] == "completed", done.get("error")
        assert cao.parent_calls == 2
        assert len(cao.worker_calls) == 1
        assert done["results"]["response"]["worker_results"][0]["output"] == "İşçi özeti"
