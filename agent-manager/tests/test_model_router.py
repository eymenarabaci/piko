"""Routing must preserve task suitability while reacting to observed quota failure."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from manager.model_router import ModelRouter, NoEligibleModel, infer_role, parse_quota_wait_seconds
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
    assert (await router.choose("architect", "cao", allow_writes=False)).model_id == "local_text"
    assert (await router.choose("planning", "cao", allow_writes=True)).provider != "ollama"
    assert [choice.model_id for choice in await router.rank("reviewer", "ao", allow_writes=True)] == ["writer_b"]
    assert not await router.rank("development", "cao", allow_writes=True,
                                 availability={"codex": False, "antigravity": False})


@pytest.mark.asyncio
async def test_small_local_model_needs_explicit_priority_for_complex_planning(tmp_path):
    models = [dict(row) for row in MODELS]
    models[2] = {**models[2], "priority": {"general": 4}}
    router = ModelRouter(models, tmp_path / "unknown-quality.sqlite")
    choice = await router.choose("architect", "cao", allow_writes=False)
    assert choice.model_id == "writer_a"  # master role has configured priority 8
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
    assert ranked[0].model_id == "writer_b"
    assert ranked[0].quota_confidence == "unknown"
    assert any("yaklaşık kullanım" in reason for reason in ranked[1].reasons)


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
async def test_verified_zero_quota_waits_until_reset_or_manual_clear(tmp_path):
    router = ModelRouter(MODELS, tmp_path / "ledger.sqlite")
    reset = datetime.now(timezone.utc) + timedelta(hours=2)
    await router.record_quota_snapshot("writer_a", 0, reset, "provider-adapter", True)
    cooldown = await router.get_cooldowns()
    assert cooldown["writer_a"]["error_kind"] == "verified_quota"
    assert (await router.choose("development", "cao", allow_writes=True)).model_id == "writer_b"
    await router.clear_cooldown("writer_a")
    assert "writer_a" not in await router.get_cooldowns()
