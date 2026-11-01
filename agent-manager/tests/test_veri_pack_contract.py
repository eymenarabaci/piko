"""Contract and offline end-to-end tests for the "veri" bridge pack.

The provider is the local workspace file system; no network or real external
service is contacted. Swapping in a live connector is an adapter change.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import bridge_packs.veri.adapter as adapter
import bridge_packs.veri.flow as flow
from bridge_packs.veri.evidence import build_evidence as sample_evidence
from manager.bridge_gate import BridgeGate
from manager.bridges import (BridgeRegistry, validate_bridge_spec,
                             validate_persona_manifest)

PACK_DIR = Path(__file__).resolve().parents[1] / "bridge_packs" / "veri"
BRIDGE_ID = "veri"
CSV_TEXT = "name,amount,region\nalpha,10,north\nbeta,20,south\ngamma,,north\n"


def _manifest() -> dict:
    return json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))


def _registry() -> BridgeRegistry:
    registry = BridgeRegistry()
    registry.register(_manifest())
    return registry


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "data.csv").write_text(CSV_TEXT, encoding="utf-8")
    return root


# ------------------------------------------------------------------- contract


def test_manifest_is_a_complete_bridge_contract():
    manifest = _manifest()
    spec = validate_bridge_spec(manifest)
    assert spec["id"] == BRIDGE_ID
    assert spec["levels"] == ["read", "draft", "apply"]
    assert "dangerous" not in spec["levels"]
    assert "dangerous" not in spec["approval"]
    assert spec["idempotency"] == {"type": "keyed", "key": "idempotency_key"}
    assert spec["evidence"]["format"] == "veri-evidence"
    assert set(spec["evidence"]["fields"]) == {"files", "sha256", "summary", "report"}
    assert spec["approval"]["apply"] == "per_call_token"
    assert set(spec["errors"]) == {"retryable", "needs-user", "external-down"}
    assert spec["adapter"] == "bridge_packs.veri.adapter:adapter"
    assert manifest["budget"] == adapter.DEFAULT_BUDGET


def test_registry_keeps_the_adapter_lazy_until_first_use():
    registry = _registry()
    assert registry.is_loaded(BRIDGE_ID) is False
    factory = registry.load_adapter(BRIDGE_ID)
    assert factory().bridge_id == BRIDGE_ID
    assert registry.is_loaded(BRIDGE_ID) is True
    assert adapter.manifest() == _manifest()


def test_error_taxonomy_keeps_source_and_user_failures_apart():
    errors = validate_bridge_spec(_manifest())["errors"]
    assert "timeout" in errors["retryable"]
    assert "source-locked" in errors["retryable"]
    assert "unsafe-target" in errors["needs-user"]
    assert "target-exists" in errors["needs-user"]
    assert "warehouse-unavailable" in errors["external-down"]
    assert not set(errors["retryable"]) & set(errors["external-down"])
    assert not set(errors["needs-user"]) & set(errors["external-down"])


def test_persona_bindings_stay_within_role_ceiling():
    manager = validate_persona_manifest(adapter.MANAGER_PERSONA, "scheduler")
    approver = validate_persona_manifest(adapter.APPROVER_PERSONA, "implementer")
    proactive = validate_persona_manifest(adapter.PROACTIVE_PERSONA, "reviewer")
    assert manager["bridges"][BRIDGE_ID] == ["read", "draft"]
    assert approver["bridges"][BRIDGE_ID] == ["read", "draft", "apply"]
    assert proactive["bridges"][BRIDGE_ID] == ["read"]
    with pytest.raises(PermissionError):
        validate_persona_manifest(
            {"role": "reviewer",
             "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}, "reviewer")


def test_apply_requires_a_single_use_approval_token():
    gate = BridgeGate(_registry())
    with pytest.raises(PermissionError):
        gate.check(adapter.APPROVER_PERSONA, BRIDGE_ID, "apply",
                   idempotency_key="k1")
    first = gate.check(adapter.APPROVER_PERSONA, BRIDGE_ID, "apply",
                       idempotency_key="k1", approval_token="token-once")
    assert first["approval_used"] is True
    assert first["ledger_key"].startswith(BRIDGE_ID + ":apply:")
    with pytest.raises(PermissionError):
        gate.check(adapter.APPROVER_PERSONA, BRIDGE_ID, "apply",
                   idempotency_key="k2", approval_token="token-once")


def test_manager_persona_cannot_apply_even_with_a_token():
    gate = BridgeGate(_registry())
    with pytest.raises(PermissionError):
        gate.check(adapter.MANAGER_PERSONA, BRIDGE_ID, "apply",
                   idempotency_key="k1", approval_token="token-once")


def test_sample_evidence_module_matches_contract():
    evidence = sample_evidence(note="smoke")
    assert evidence["format"] == "veri-evidence"
    assert evidence["bridge"] == BRIDGE_ID
    assert evidence["ok"] is True


# --------------------------------------------------------------- read level


def test_read_profiles_csv_columns_and_hashes_the_source(workspace: Path):
    result = adapter.read_dataset(workspace, "data.csv")
    assert result["format"] == "csv"
    assert result["row_count"] == 3
    assert result["column_count"] == 3
    assert result["column_names"] == ["name", "amount", "region"]
    assert result["sha256"] == hashlib.sha256(
        (workspace / "data.csv").read_bytes()).hexdigest()
    amount = next(column for column in result["columns"] if column["name"] == "amount")
    assert amount["kind"] == "number"
    assert amount["nulls"] == 1
    assert amount["min"] == 10.0
    assert amount["max"] == 20.0
    assert amount["mean"] == 15.0
    assert len(result["summary"].splitlines()) == 3
    with pytest.raises(adapter.VeriError) as error:
        adapter.read_dataset(workspace, "../secret.csv")
    assert adapter.error_payload(error.value)["error_class"] == "needs-user"


def test_read_accepts_json_and_rejects_unsupported_formats(workspace: Path):
    (workspace / "rows.json").write_text(
        json.dumps([{"id": 1, "value": "a"}, {"id": 2, "value": "b"}]),
        encoding="utf-8")
    result = adapter.read_dataset(workspace, "rows.json")
    assert result["format"] == "json"
    assert result["row_count"] == 2
    assert result["column_names"] == ["id", "value"]
    (workspace / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(adapter.VeriError):
        adapter.read_dataset(workspace, "notes.txt")


# -------------------------------------------------------------- draft level


def test_draft_builds_grouped_report_in_memory_only(workspace: Path):
    before = sorted(path.name for path in workspace.iterdir())
    draft = flow.draft_payload({
        "workspace": str(workspace), "path": "data.csv", "title": "Satis Analizi",
        "group_by": "region", "metric": "sum", "value_column": "amount"})
    assert sorted(path.name for path in workspace.iterdir()) == before, \
        "taslak disk yazmamali"
    assert set(draft["files"]) == {"satis-analizi.md", "satis-analizi-ozet.csv",
                                   "satis-analizi-gruplar.csv"}
    assert all(len(value) == 64 for value in draft["hashes"].values())
    markdown = draft["files"]["satis-analizi.md"].decode("utf-8")
    assert markdown.startswith("# Satis Analizi")
    assert draft["dataset"]["sha256"] in markdown
    assert draft["analysis"]["groups"] == [
        {"group": "north", "count": 2, "value": 10.0},
        {"group": "south", "count": 1, "value": 20.0},
    ]
    assert len(draft["summary"].splitlines()) == 3


def test_draft_requires_a_path(workspace: Path):
    with pytest.raises(adapter.VeriError) as error:
        adapter.build_draft({"workspace": str(workspace)})
    assert adapter.error_payload(error.value)["error_class"] == "needs-user"


# ---------------------------------------------------------- apply / evidence


def test_delivery_read_draft_apply_evidence_end_to_end(workspace: Path):
    gate = BridgeGate(_registry())
    request = {"workspace": str(workspace), "path": "data.csv",
               "title": "Haftalik Satis"}

    with pytest.raises(PermissionError):
        flow.deliver(gate, persona=adapter.MANAGER_PERSONA, request=request,
                     target="raporlar", idempotency_key="veri-1")
    assert not (workspace / "raporlar").exists(), "onaysiz apply yazmamali"

    draft = flow.draft_payload(request)
    approval = flow.issue_approval(draft)
    result = flow.deliver(
        gate, persona=adapter.MANAGER_PERSONA, request=request, target="raporlar",
        approval_token=approval["approval_token"], idempotency_key="veri-1",
        workspace=str(workspace))
    assert result["ok"] is True, result
    evidence = result["evidence"]
    assert evidence["format"] == "veri-evidence"
    assert evidence["bridge"] == BRIDGE_ID
    assert evidence["ok"] is True
    assert evidence["files"]
    for entry in evidence["files"]:
        written = workspace / entry["path"]
        assert written.is_file()
        assert hashlib.sha256(written.read_bytes()).hexdigest() == entry["sha256"]
        assert evidence["sha256"][entry["path"]] == entry["sha256"]
    assert evidence["report"]["path"].endswith(".md")
    assert evidence["report"]["preview"].startswith("# Haftalik Satis")
    assert evidence["report"]["rows"] == 3
    assert evidence["report"]["columns"] == ["name", "amount", "region"]
    assert len(evidence["summary"].splitlines()) == 3
    assert result["decision"]["approval_used"] is True
    assert any("sha256" in line for line in result["lines"])

    with pytest.raises(PermissionError):
        flow.deliver(
            gate, persona=adapter.MANAGER_PERSONA, request=request,
            target="raporlar-2", approval_token=approval["approval_token"],
            idempotency_key="veri-2", workspace=str(workspace))
    assert not (workspace / "raporlar-2").exists()


def test_apply_refuses_outside_targets_and_silent_overwrites(workspace: Path):
    draft = flow.draft_payload({"workspace": str(workspace), "path": "data.csv",
                                "title": "Sinir Testi"})
    applied = adapter.apply_report(workspace, "raporlar", draft["files"],
                                   meta=draft["dataset"])
    assert len(applied["files"]) == 2
    before = {entry["path"]: entry["sha256"] for entry in applied["files"]}

    with pytest.raises(adapter.VeriError) as error:
        adapter.apply_report(workspace, "raporlar", draft["files"])
    assert adapter.error_payload(error.value)["error_class"] == "needs-user"

    with pytest.raises(adapter.VeriError):
        adapter.apply_report(workspace, "../outside", draft["files"])
    assert not (workspace.parent / "outside").exists()

    assert {entry["path"]: entry["sha256"]
            for entry in applied["files"]} == before


# -------------------------------------------------------------------- budget


async def test_default_budget_profile_applies_to_the_ledger():
    profile = json.loads((PACK_DIR / "budget_profile.json").read_text(encoding="utf-8"))
    assert {key: profile[key] for key in adapter.DEFAULT_BUDGET} == adapter.DEFAULT_BUDGET

    class FakeLedger:
        def __init__(self):
            self.called = None

        async def configure_budget(self, scope, **limits):
            self.called = (scope, limits)
            return {"scope": scope, **limits}

    ledger = FakeLedger()
    result = await adapter.configure_budget(ledger)
    assert ledger.called == ("bridge:veri", {
        "token_limit": 40000, "seconds_limit": 900, "repairs_limit": 2})
    assert result["scope"] == "bridge:veri"


def test_error_payloads_map_to_distinct_classes():
    assert adapter.error_payload(
        adapter.RetryableVeriError("x"))["error_class"] == "retryable"
    assert adapter.error_payload(
        adapter.ExternalVeriError("x"))["error_class"] == "external-down"
    assert adapter.error_payload(adapter.VeriError("x"))["error_class"] == "needs-user"
