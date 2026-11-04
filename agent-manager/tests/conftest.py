"""Keep operator-local execution restrictions out of synthetic test fixtures."""
import pytest
@pytest.fixture(autouse=True)
def isolated_operator_policy(tmp_path, monkeypatch):
    from manager import execution_policy
    monkeypatch.setattr(execution_policy, "POLICY_PATH", tmp_path / "operator-policy.json")
