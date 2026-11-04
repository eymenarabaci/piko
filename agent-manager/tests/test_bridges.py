import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from manager.bridge_gate import ApprovalRequired, BridgeDenied, BridgeGate
from manager.bridges import (
    BridgeRegistry,
    role_level_ceiling,
    validate_bridge_spec,
    validate_persona_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD_PATH = ROOT / "tools" / "bridge_pack_scaffold.py"


def _load_scaffold():
    spec = importlib.util.spec_from_file_location("bridge_pack_scaffold", SCAFFOLD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scaffold = _load_scaffold()


def _spec(**overrides):
    base = {
        "id": "demo.local",
        "levels": ["read"],
        "idempotency": {"type": "none"},
        "evidence": {"format": "demo-evidence", "fields": ["summary"]},
        "errors": {
            "retryable": ["timeout"],
            "needs-user": ["missing-input"],
            "external-down": ["connection-refused"],
        },
        "approval": {},
    }
    base.update(overrides)
    return base


def _apply_spec(**overrides):
    base = _spec(
        levels=["read", "apply"],
        idempotency={"type": "keyed"},
        approval={"apply": "approved"},
    )
    base.update(overrides)
    return base


def _dangerous_spec(**overrides):
    base = _spec(
        levels=["read", "dangerous"],
        idempotency={"type": "keyed"},
        approval={"dangerous": "per_call_token"},
    )
    base.update(overrides)
    return base


def test_registry_accepts_complete_contract():
    registry = BridgeRegistry()
    stored = registry.register(_apply_spec())
    assert stored["id"] == "demo.local"
    assert stored["levels"] == ["read", "apply"]
    assert registry.get("demo.local")["evidence"]["format"] == "demo-evidence"
    assert len(registry) == 1
    assert "demo.local" in registry


def test_missing_evidence_is_rejected():
    spec = _spec()
    del spec["evidence"]
    with pytest.raises(ValueError, match="evidence"):
        validate_bridge_spec(spec)


def test_missing_error_class_is_rejected():
    spec = _spec()
    del spec["errors"]["external-down"]
    with pytest.raises(ValueError, match="external-down"):
        validate_bridge_spec(spec)


def test_unknown_permission_level_is_rejected():
    with pytest.raises(ValueError, match="Bilinmeyen"):
        validate_bridge_spec(_spec(levels=["read", "root"]))


def test_apply_without_idempotency_is_rejected():
    spec = _spec(levels=["apply"], approval={"apply": "approved"})
    with pytest.raises(ValueError, match="idempotency"):
        validate_bridge_spec(spec)


def test_dangerous_without_per_call_token_policy_is_rejected():
    spec = _spec(
        levels=["dangerous"],
        idempotency={"type": "keyed"},
        approval={"dangerous": "approved"},
    )
    with pytest.raises(ValueError, match="dangerous"):
        validate_bridge_spec(spec)


def test_registry_is_lazy_until_adapter_is_requested(tmp_path, monkeypatch):
    module_name = "lazy_bridge_adapter_probe"
    (tmp_path / f"{module_name}.py").write_text(
        "def build():\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    sys.modules.pop(module_name, None)

    registry = BridgeRegistry()
    registry.register(_spec(adapter=f"{module_name}:build"))
    assert module_name not in sys.modules
    assert registry.is_loaded("demo.local") is False
    assert registry.catalog()[0]["loaded"] is False
    assert module_name not in sys.modules

    adapter = registry.load_adapter("demo.local")
    assert module_name in sys.modules
    assert adapter() == {"ok": True}
    assert registry.is_loaded("demo.local") is True
    sys.modules.pop(module_name, None)


def test_duplicate_bridge_requires_replace():
    registry = BridgeRegistry()
    registry.register(_spec())
    with pytest.raises(ValueError, match="zaten"):
        registry.register(_spec())
    registry.register(_spec(version="2.0.0"), replace=True)
    assert registry.get("demo.local")["version"] == "2.0.0"


def test_role_ceiling_blocks_reviewer_apply():
    persona = {"role": "reviewer", "bridges": {"demo.local": {"levels": ["read", "apply"]}}}
    with pytest.raises(PermissionError, match="tavan"):
        validate_persona_manifest(persona, "reviewer")


def test_role_ceiling_allows_implementer_apply():
    persona = {"role": "implementer", "bridges": {"demo.local": {"levels": ["read", "apply"]}}}
    validated = validate_persona_manifest(persona, "implementer")
    assert validated["bridges"]["demo.local"] == ["read", "apply"]
    assert role_level_ceiling("implementer") >= {"read", "draft", "apply"}


def test_persona_cannot_declare_dangerous():
    persona = {"role": "implementer", "bridges": {"demo.local": {"levels": ["dangerous"]}}}
    with pytest.raises(PermissionError):
        validate_persona_manifest(persona, "implementer")


def test_gate_rejects_dangerous_without_approval():
    registry = BridgeRegistry()
    registry.register(_dangerous_spec())
    gate = BridgeGate(registry)
    with pytest.raises(ApprovalRequired):
        gate.check("implementer", "demo.local", "dangerous", idempotency_key="k1")


def test_gate_dangerous_token_is_single_use():
    registry = BridgeRegistry()
    registry.register(_dangerous_spec())
    gate = BridgeGate(registry)
    token = gate.issue_approval("demo.local", "dangerous")
    first = gate.check("implementer", "demo.local", "dangerous",
                       idempotency_key="k1", approval_token=token)
    assert first["approval_used"] is True
    assert first["ledger_key"].startswith("demo.local:dangerous:")
    with pytest.raises(ApprovalRequired):
        gate.check("implementer", "demo.local", "dangerous",
                   idempotency_key="k2", approval_token=token)


def test_gate_requires_persona_bridge_grant():
    registry = BridgeRegistry()
    registry.register(_spec())
    gate = BridgeGate(registry)
    with pytest.raises(BridgeDenied):
        gate.check({"role": "reviewer", "bridges": {}}, "demo.local", "read")
    allowed = gate.check(
        {"role": "reviewer", "bridges": {"demo.local": {"levels": ["read"]}}},
        "demo.local", "read")
    assert allowed["ledger_key"].startswith("demo.local:read:")


def test_gate_rejects_level_bridge_does_not_expose():
    registry = BridgeRegistry()
    registry.register(_spec())
    gate = BridgeGate(registry)
    with pytest.raises(BridgeDenied):
        gate.check("implementer", "demo.local", "apply")


def test_gate_apply_produces_ledger_key_and_needs_idempotency():
    registry = BridgeRegistry()
    registry.register(_apply_spec())
    gate = BridgeGate(registry)
    persona = {"role": "implementer", "bridges": {"demo.local": {"levels": ["read", "apply"]}}}
    with pytest.raises(ValueError, match="idempotency"):
        gate.check(persona, "demo.local", "apply")
    result = gate.check(persona, "demo.local", "apply",
                        idempotency_key="apply-key", payload={"file": "a.txt"})
    assert result["ledger_key"].startswith("demo.local:apply:")
    assert result["approval_used"] is False
    assert result["payload_hash"]


@pytest.mark.parametrize(
    ("name", "levels"),
    [
        ("local-read", ["read"]),
        ("local-apply", ["read", "apply"]),
        ("local-dangerous", ["read", "dangerous"]),
    ],
)
def test_scaffold_pack_passes_its_own_contract(tmp_path, name, levels):
    paths = scaffold.write_pack(name, levels, tmp_path)

    assert paths["manifest"].is_file()
    assert paths["evidence"].is_file()
    assert paths["contract_test"].is_file()
    assert paths["persona_binding"].is_file()

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    validate_bridge_spec(manifest)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(paths["contract_test"])],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_scaffold_dangerous_pack_manifest_uses_token_policy(tmp_path):
    paths = scaffold.write_pack("local-dangerous", ["read", "dangerous"], tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["approval"]["dangerous"] == "per_call_token"
    assert manifest["idempotency"]["type"] == "keyed"
    binding = paths["persona_binding"].read_text(encoding="utf-8")
    assert "local-dangerous:" in binding
    assert "levels: [read]" in binding
    assert "levels: [read, dangerous]" not in binding


def test_scaffold_rejects_unknown_level():
    with pytest.raises(ValueError, match="Bilinmeyen"):
        scaffold.build_manifest("bad-pack", ["read", "root"])
