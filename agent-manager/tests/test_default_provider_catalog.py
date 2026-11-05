import json
from manager import execution_policy
from manager.model_discovery import load_models_file, merge_models, save_models_file


def test_fresh_install_loads_generic_seed_without_local_preferences(tmp_path):
    seed = [{"id": "generic", "provider": "opencode", "hidden": False}]
    (tmp_path / "models.example.json").write_text(json.dumps({"models": seed}))
    assert load_models_file(tmp_path / "models.json") == seed
    save_models_file(tmp_path / "models.json", [{**seed[0], "hidden": True}])
    assert load_models_file(tmp_path / "models.json")[0]["hidden"] is True
    assert json.loads((tmp_path / "models.example.json").read_text())["models"][0]["hidden"] is False


def test_refresh_preserves_local_visibility():
    local = [{"id": "generic", "provider": "opencode", "hidden": True}]
    fresh = [{"id": "generic", "provider": "opencode", "hidden": False}]
    assert merge_models(local, fresh)[0]["hidden"] is True


def test_no_operator_policy_allows_all_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_policy, "POLICY_PATH", tmp_path / "execution_policy.json")
    for provider in ["opencode", "codex", "claude-code", "antigravity", "ollama", "custom-provider"]:
        assert execution_policy.model_allowed({"provider": provider})
