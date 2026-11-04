"""Contract test for the office bridge pack."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_persona_manifest

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
EVIDENCE_PATH = Path(__file__).with_name("evidence.py")
BRIDGE_ID = 'office'
PACK_LEVELS = ['read', 'draft', 'apply']
BINDING_ROLE = 'implementer'
BINDING_LEVELS = ['read', 'draft', 'apply']
EVIDENCE_FORMAT = 'office-evidence'


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _registry():
    registry = BridgeRegistry()
    registry.register(_manifest())
    return registry


def _persona():
    return {"role": BINDING_ROLE, "bridges": {BRIDGE_ID: {"levels": BINDING_LEVELS}}}


def test_manifest_satisfies_contract():
    spec = _registry().get(BRIDGE_ID)
    assert spec["id"] == BRIDGE_ID
    assert set(spec["levels"]) == set(PACK_LEVELS)
    assert {"retryable", "needs-user", "external-down"} <= set(spec["errors"])
    assert spec["evidence"]["format"] == EVIDENCE_FORMAT


def test_persona_manifest_stays_within_role_ceiling():
    validated = validate_persona_manifest(_persona(), BINDING_ROLE)
    assert validated["bridges"][BRIDGE_ID] == BINDING_LEVELS


def test_evidence_generator_matches_contract():
    spec = importlib.util.spec_from_file_location("pack_evidence", EVIDENCE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = module.build_evidence(note="smoke")
    assert evidence["format"] == EVIDENCE_FORMAT
    assert evidence["bridge"] == BRIDGE_ID
    assert evidence["ok"] is True


def test_gate_returns_ledger_key_for_allowed_level():
    gate = BridgeGate(_registry())
    token = gate.issue_approval(BRIDGE_ID, "apply")
    result = gate.check(_persona(), BRIDGE_ID, 'apply',
                        idempotency_key="smoke-key",
                        approval_token=token)
    assert result["ledger_key"].startswith(BRIDGE_ID + ":" + 'apply' + ":")
