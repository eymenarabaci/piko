import pytest
from pydantic import ValidationError

from manager.planning_policy import planning_guidance
from manager.schema import RunRequest


def test_default_and_roundtrip():
    assert RunRequest(prompt="Örnek").planning_level == "fast"
    for level in ("fast", "balanced", "detailed"):
        request = RunRequest(prompt="Örnek", planning_level=level)
        assert request.model_dump()["planning_level"] == level
        assert "alt ajan sayısını artırma talimatı değildir" in planning_guidance(level)
    assert len({planning_guidance(level) for level in ("fast", "balanced", "detailed")}) == 3


def test_invalid_level_rejected():
    with pytest.raises(ValidationError):
        RunRequest(prompt="Örnek", planning_level="unlimited")
