"""Operator execution policy, checked on every selection and invocation."""
import json
from pathlib import Path
POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "execution_policy.json"
def model_allowed(model):
    if not POLICY_PATH.exists():
        return True
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8-sig"))
        allowed = policy["allowed_providers"]
        if not isinstance(allowed, list) or not allowed:
            return False
        return model.get("provider") in allowed
    except (OSError, ValueError, KeyError, TypeError):
        return False
