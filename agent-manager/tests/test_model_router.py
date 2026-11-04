"""Routing must preserve task suitability while reacting to observed quota failure."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from manager.model_router import ModelRouter, NoEligibleModel, infer_role, parse_quota_wait_seconds
from manager.app import ollama_model_profile
from manager.outcomes import ensure_usable_result


MODELS = [
    {"id": "writer_a", "provider": "antigravity", "capabilities": ["general", "development", "master"],
     "priority": {"general": 5, "development": 5, "master": 8}, "execution_supported": True},
    {"id": "writer_b", "provider": "codex", "capabilities": ["general", "development", "reviewer", "master"],
     "priority": {"general": 6, "development": 6, "reviewer": 2, "master": 9}, "execution_supported": True},
    {"id": "local_text", "provider": "ollama", "capabilities": ["general", "architect"],
     "priority": {"general": 4, "architect": 1}, "execution_supported": True},
    {"id": "off", "provider": "codex", "capabilities": ["general"],
     "priority": {"general": 0}, "execution_supported": False},
]


@pytest.mark.asyncio
async def test_quota_failure_routes_to_compatible_alternative(tmp_path):
    db = tmp_path / "ledger.sqlite"
    router = ModelRouter(MODELS, db)
    initial = await router.choose("development", "cao", allow_writes=True,
                                  availability={"codex": True, "antigravity": True, "ollama": True})
    assert initial.model_id == "writer_a"
    assert await router.record_failure("writer_a", "Sağlayıcı kullanım kotası doldu.") == "quota"
    persisted = ModelRouter(MODELS, db)
    fallback = await persisted.choose("development", "cao", allow_writes=True)
    assert fallback.model_id == "writer_b"
    assert fallback.quota_confidence == "unknown"
    assert await persisted.record_failure("writer_b", "rate limit", retry_after_seconds=60) == "rate_limit"
    with pytest.raises(NoEligibleModel):
        await persisted.choose("development", "cao", allow_writes=True)
    await persisted.record_success("writer_a")
    assert (await persisted.choose("development", "cao", allow_writes=True)).model_id == "writer_a"


@pytest.mark.asyncio
async def test_role_backend_and_file_permissions_are_hard_filters(tmp_path):
    router = ModelRouter(MODELS, tmp_path / "ledger.sqlite")
    assert infer_role("planner", "Yeni iş adımları") == "architect"
    assert infer_role("writer", "Başlık yaz") == "general"
    assert infer_role("security_auditor", "Kod denetle") == "reviewer"
    assert infer_role("feature_developer", "Şirket içi etkinlik planlama") == "development"
    assert infer_role("frontend_architect", "Takvim bileşenini planla ve yaz") == "development"
    assert (await router.choose("architect", "cao", allow_writes=False)).model_id == "local_text"
    assert (await router.choose("planning", "cao", allow_writes=True)).provider == "ollama"
    assert [choice.model_id for choice in await router.rank("reviewer", "ao", allow_writes=True)] == ["writer_b"]
    assert not await router.rank("development", "cao", allow_writes=True,
                                 availability={"codex": False, "antigravity": False})


def test_vision_ollama_model_is_not_assigned_coding_or_architecture():
    capabilities, _, modalities = ollama_model_profile(
        'moondream:latest', {'details': {'family': 'moondream'}})
    assert 'development' not in capabilities
    assert 'architect' not in capabilities
    assert modalities == ['text', 'image']


@pytest.mark.asyncio
async def test_small_local_model_needs_explicit_priority_for_complex_planning(tmp_path):
    models = [dict(row) for row in MODELS]
    models[2] = {**models[2], "priority": {"general": 4}}
    router = ModelRouter(models, tmp_path / "unknown-quality.sqlite")
    choice = await router.choose("architect", "cao", allow_writes=False)
    assert choice.model_id == "writer_b"  # Codex priority is normalized to 7.
    assert choice.quota_confidence == "unknown"
    assert any("görev kalitesi ölçülmedi" in reason for reason in choice.reasons)
    # An operator can explicitly mark local planning as a better fit.
    preferred = ModelRouter(MODELS, tmp_path / "explicit-priority.sqlite")
    assert (await preferred.choose("architect", "cao", allow_writes=False)).model_id == "local_text"


@pytest.mark.asyncio
async def test_verified_quota_changes_order_without_pretending_unknown_is_known(tmp_path):
    router = ModelRouter(MODELS, tmp_path / "ledger.sqlite")
    now = datetime.now(timezone.utc)
    await router.record_quota_snapshot("writer_a", 0.01, now + timedelta(hours=1), "real-adapter", True)
    await router.record_quota_snapshot("writer_b", 0.9, now + timedelta(hours=1), "real-adapter", True)
    ranked = await router.rank("development", "direct", allow_writes=True, estimated_tokens=2000)
    assert ranked[0].model_id == "writer_b"
    assert ranked[0].quota_confidence == "verified"
    assert "doğrulanmış" in " ".join(ranked[0].reasons)
    assert ranked[1].model_id == "writer_a"
    with pytest.raises(ValueError):
        await router.record_quota_snapshot("writer_a", 1.5, None, "adapter", True)


@pytest.mark.asyncio
async def test_observed_token_load_is_only_a_routing_proxy(tmp_path):
    db = tmp_path / "ledger.sqlite"
    router = ModelRouter(MODELS, db)
    await router.rank("general")  # creates routing table
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE token_ledger (
            id INTEGER PRIMARY KEY,run_id TEXT,step_id TEXT,model_id TEXT,
            input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,timestamp TEXT)""")
        connection.execute("INSERT INTO token_ledger VALUES (1,'r','s','writer_a',5000,1000,6000,?)",
                           (datetime.now(timezone.utc).isoformat(),))
    ranked = await router.rank("general", "direct", allow_writes=True)
    assert ranked[0].model_id == "local_text"
    assert ranked[0].quota_confidence == "local"
    assert any("yaklaşık kullanım" in reason for choice in ranked for reason in choice.reasons)


@pytest.mark.asyncio
async def test_non_quota_failure_does_not_disable_model(tmp_path):
    router = ModelRouter(MODELS, tmp_path / "ledger.sqlite")
    assert await router.record_failure("writer_a", RuntimeError("Test failed")) is None
    assert (await router.choose("development", "cao", allow_writes=True)).model_id == "writer_a"
    assert await router.record_failure("writer_a", "You have exceeded your current quota") == "quota"


@pytest.mark.asyncio
async def test_quota_cools_only_the_runtime_model_not_all_antigravity(tmp_path):
    models = [
        {"id": "opus-high", "provider": "antigravity", "base_model": "claude-opus",
         "capabilities": ["development"], "priority": {"development": 3}},
        {"id": "opus-low", "provider": "antigravity", "base_model": "claude-opus",
         "capabilities": ["development"], "priority": {"development": 4}},
        {"id": "flash", "provider": "antigravity", "base_model": "gemini-flash",
         "capabilities": ["development"], "priority": {"development": 5}},
        {"id": "codex", "provider": "codex", "base_model": "gpt-6-sol",
         "capabilities": ["development"], "priority": {"development": 7}},
    ]
    router = ModelRouter(models, tmp_path / "ledger.sqlite")
    assert (await router.choose("development", "cao", allow_writes=True)).model_id == "opus-high"
    assert await router.record_failure("opus-high", "Individual quota reached") == "quota"
    ranked = await router.rank("development", "cao", allow_writes=True)
    assert [choice.model_id for choice in ranked] == ["flash", "codex"]
    await router.record_success("opus-low")
    assert (await router.choose("development", "cao", allow_writes=True)).model_id == "opus-high"


@pytest.mark.asyncio
async def test_all_compatible_models_cooling_wait_instead_of_running_early(tmp_path):
    router = ModelRouter(MODELS, tmp_path / "ledger.sqlite")
    await router.record_failure("writer_a", "Individual quota reached. Resets in 4h7m37s.")
    await router.record_failure("writer_b", "Individual quota reached. Resets in 2h5m.")
    assert await router.rank("development", "cao", allow_writes=True) == []
    with pytest.raises(NoEligibleModel):
        await router.choose("development", "cao", allow_writes=True)
    nearest = await router.next_cooldown("development", "cao", allow_writes=True)
    assert nearest["model_id"] == "writer_b"
    assert nearest["remaining_seconds"] > 2 * 3600
    assert await router.next_cooldown("reviewer", "cao", allow_writes=False)
    assert parse_quota_wait_seconds("Resets in 111h7m37s") == 400057
    with pytest.raises(RuntimeError, match="kota"):
        ensure_usable_result({"status": "completed", "output": "Individual quota reached. Resets in 2h."})


@pytest.mark.asyncio
async def test_account_quota_cools_the_whole_codex_family_not_other_providers(tmp_path):
    models = [
        {"id": "codex", "provider": "codex", "base_model": "gpt-6.1-sol", "shared_quota_account": True,
         "capabilities": ["development"], "priority": {"development": 3}},
        {"id": "gpt-6-sol", "provider": "codex", "base_model": "gpt-6-sol", "shared_quota_account": True,
         "capabilities": ["development"], "priority": {"development": 4}},
        {"id": "gpt-5.6-terra", "provider": "codex", "base_model": "gpt-5.6-terra", "shared_quota_account": True,
         "capabilities": ["development"], "priority": {"development": 5}},
        {"id": "opus", "provider": "claude-code", "base_model": "claude-opus", "shared_quota_account": True,
         "capabilities": ["development"], "priority": {"development": 6}},
        {"id": "flash", "provider": "antigravity", "base_model": "gemini-flash",
         "capabilities": ["development"], "priority": {"development": 7}},
    ]
    router = ModelRouter(models, tmp_path / "ledger.sqlite")
    assert await router.record_failure("gpt-5.6-terra", "You've hit your usage limit") == "quota"
    ranked = await router.rank("development", "direct", allow_writes=True)
    assert {choice.model_id for choice in ranked} == {"flash", "opus"}
    cooled = set(await router.get_cooldowns())
    assert {"codex", "gpt-6-sol", "gpt-5.6-terra"} <= cooled
    assert "flash" not in cooled and "opus" not in cooled

    # A different subscription account cools only its own family.
    assert await router.record_failure("opus", "You've hit your usage limit") == "quota"
    assert [choice.model_id for choice in await router.rank("development", "direct", allow_writes=True)] == ["flash"]
    # A healthy cross-provider alternative remains usable; do not wait for quota.
    assert await router.next_cooldown("development", "direct", allow_writes=True) is None

    # A success on one codex model proves the shared account is live again.
    await router.record_success("codex")
    assert not ({"codex", "gpt-6-sol", "gpt-5.6-terra"} & set(await router.get_cooldowns()))
    assert "opus" in await router.get_cooldowns()


@pytest.mark.asyncio
async def test_codex_rate_limit_cools_only_the_hit_model(tmp_path):
    models = [
        {"id": "gpt-6-sol", "provider": "codex", "base_model": "gpt-6-sol", "shared_quota_account": True,
         "capabilities": ["development"], "priority": {"development": 3}},
        {"id": "gpt-6-luna", "provider": "codex", "base_model": "gpt-6-luna", "shared_quota_account": True,
         "capabilities": ["development"], "priority": {"development": 4}},
    ]
    router = ModelRouter(models, tmp_path / "ledger.sqlite")
    assert await router.record_failure("gpt-6-sol", "429 Too Many Requests") == "rate_limit"
    assert [choice.model_id for choice in await router.rank("development", "direct", allow_writes=True)] == ["gpt-6-luna"]


@pytest.mark.asyncio
async def test_writer_holds_for_cooling_remote_instead_of_weak_local(tmp_path):
    models = [
        {"id": "codex_a", "provider": "codex", "capabilities": ["development"],
         "priority": {"development": 1}},
        {"id": "local_writer", "provider": "ollama", "capabilities": ["development"],
         "priority": {"development": 5}},
    ]
    router = ModelRouter(models, tmp_path / "ledger.sqlite")
    # Fresh: the capable remote is chosen even with the hold policy.
    assert (await router.choose("development", "direct", allow_writes=True,
                                hold_for_cooling_capable=True)).model_id == "codex_a"

    assert await router.record_failure("codex_a", "You've hit your usage limit") == "quota"
    # Without the policy the engine would fall back to the weak local writer.
    assert [c.model_id for c in await router.rank("development", "direct", allow_writes=True)] == ["local_writer"]
    # With the policy the writer waits for the cooling remote instead of failing.
    assert await router.rank("development", "direct", allow_writes=True,
                             hold_for_cooling_capable=True) == []
    with pytest.raises(NoEligibleModel):
        await router.choose("development", "direct", allow_writes=True, hold_for_cooling_capable=True)
    # Waiting uses the same explicit hold policy as ranking. Default still permits local fallback.
    assert await router.next_cooldown("development", "direct", allow_writes=True) is None
    assert await router.next_cooldown("development", "direct", allow_writes=True,
                                      hold_for_cooling_capable=True)


@pytest.mark.asyncio
async def test_hold_for_cooling_capable_lets_local_run_when_nothing_cools(tmp_path):
    models = [
        {"id": "local_writer", "provider": "ollama", "capabilities": ["development"],
         "priority": {"development": 5}},
        {"id": "codex_a", "provider": "codex", "capabilities": ["development"],
         "priority": {"development": 1}, "hidden": True},
    ]
    router = ModelRouter(models, tmp_path / "ledger.sqlite")
    # Only a local model is eligible and no remote is cooling -> local must run.
    assert (await router.choose("development", "direct", allow_writes=True,
                                hold_for_cooling_capable=True)).model_id == "local_writer"


@pytest.mark.asyncio
async def test_verified_zero_quota_waits_until_reset_or_manual_clear(tmp_path):
    router = ModelRouter(MODELS, tmp_path / "ledger.sqlite")
    reset = datetime.now(timezone.utc) + timedelta(hours=2)
    await router.record_quota_snapshot("writer_a", 0, reset, "provider-adapter", True)
    cooldown = await router.get_cooldowns()
    assert cooldown["writer_a"]["error_kind"] == "verified_quota"
    assert (await router.choose("development", "cao", allow_writes=True)).model_id == "writer_b"
    await router.clear_cooldown("writer_a")
    assert "writer_a" not in await router.get_cooldowns()


@pytest.mark.asyncio
async def test_concrete_task_requirements_exclude_incompatible_models(tmp_path):
    models = [
        {"id": "text_only", "provider": "codex", "capabilities": ["development"],
         "input_modalities": ["text"], "context_window_tokens": 8192,
         "priority": {"development": 1}},
        {"id": "vision", "provider": "codex", "capabilities": ["development"],
         "input_modalities": ["text", "image"], "context_window_tokens": 32768,
         "priority": {"development": 2}},
        {"id": "read_only", "provider": "ollama", "capabilities": ["development"],
         "input_modalities": ["text", "image"], "context_window_tokens": 32768,
         "supports_writes": False, "mcp_supported": False,
         "priority": {"development": 0}},
    ]
    router = ModelRouter(models, tmp_path / "requirements.sqlite")
    ranked = await router.rank("development", "cao", allow_writes=True,
                               required_modalities=("IMAGE",), required_tools=True,
                               min_context_tokens=16000)
    assert [choice.model_id for choice in ranked] == ["vision"]
    assert await router.rank("development", "direct", required_tools=True) == []
    assert await router.rank("development", "cao", min_context_tokens=50000) == []


@pytest.mark.asyncio
async def test_only_verified_task_outcomes_affect_ranking_after_enough_samples(tmp_path):
    models = [
        {"id": "a", "provider": "antigravity", "capabilities": ["development"],
         "priority": {"development": 5}},
        {"id": "b", "provider": "antigravity", "capabilities": ["development"],
         "priority": {"development": 6}},
    ]
    router = ModelRouter(models, tmp_path / "outcomes.sqlite")
    assert (await router.choose("development", task_kind="coding")).model_id == "a"
    with pytest.raises(ValueError, match="doğrulanmış"):
        await router.record_outcome("b", "coding", True, 12, 300, verified=False)
    for _ in range(4):
        await router.record_outcome("b", "coding", True, 12, 300, verified=True)
    assert (await router.choose("development", task_kind="coding")).model_id == "a"
    await router.record_outcome("b", "coding", True, 12, 300, verified=True)
    choice = await router.choose("development", task_kind="coding")
    assert choice.model_id == "b"
    assert any("5/5" in reason for reason in choice.reasons)
    assert (await router.choose("development", task_kind="review")).model_id == "a"


@pytest.mark.asyncio
async def test_latency_only_compares_models_with_measured_outcomes(tmp_path):
    models = [
        {"id": "slow", "provider": "antigravity", "capabilities": ["reviewer"],
         "priority": {"reviewer": 5}},
        {"id": "fast", "provider": "antigravity", "capabilities": ["reviewer"],
         "priority": {"reviewer": 6}},
    ]
    router = ModelRouter(models, tmp_path / "latency.sqlite")
    for _ in range(5):
        await router.record_outcome("slow", "review", True, 120, 100, verified=True)
        await router.record_outcome("fast", "review", True, 10, 100, verified=True)
    assert (await router.choose("reviewer", task_kind="review")).model_id == "fast"


@pytest.mark.asyncio
async def test_calibration_utility_orders_strong_unknown_weak_on_one_scale(tmp_path):
    models = []
    for name, weight in [('strong', 0.9), ('unknown', None), ('weak', 0.2)]:
        model = {'id': name, 'provider': 'codex', 'capabilities': ['development'],
                 'priority': {'development': 7}}
        if weight is not None:
            model.update(role_weights={'development': weight}, provenance={'calibrated': True})
        models.append(model)
    router = ModelRouter(models, tmp_path / 'calibration.sqlite')
    ranked = await router.rank('development')
    assert [choice.model_id for choice in ranked] == ['strong', 'unknown', 'weak']
    # Hard quota exclusion remains authoritative even for the best calibrated model.
    await router.record_failure('strong', 'Individual quota reached')
    assert [choice.model_id for choice in await router.rank('development')] == ['unknown', 'weak']


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_overrides, availability", [
    ({}, {"codex": False, "ollama": True}),
    ({"hidden": True}, None),
    ({"supports_writes": False}, None),
])
async def test_hold_does_not_wait_for_unavailable_or_incompatible_remote(tmp_path, remote_overrides, availability):
    models = [
        {"id": "remote", "provider": "codex", "capabilities": ["development"],
         "priority": {"development": 1}, **remote_overrides},
        {"id": "local", "provider": "ollama", "capabilities": ["development"],
         "priority": {"development": 5}},
    ]
    router = ModelRouter(models, tmp_path / "hold-suitability.sqlite")
    await router.record_failure("remote", "You've hit your usage limit")
    ranked = await router.rank("development", "direct", allow_writes=True,
                               availability=availability, hold_for_cooling_capable=True)
    assert [choice.model_id for choice in ranked] == ["local"]
    assert await router.next_cooldown("development", "direct", allow_writes=True,
                                      availability=availability, hold_for_cooling_capable=True) is None
