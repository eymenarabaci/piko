"""Bridge pack factory: one command produces a complete domain-pack skeleton.

Generated pack contains a contract manifest, a sample evidence generator, a
self-contained contract test and a persona binding block. The pack is accepted
into the catalog only when its own contract test is green.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

LEVELS = ("read", "draft", "apply", "dangerous")
LEVEL_SET = frozenset(LEVELS)
ERROR_SAMPLES = {
    "retryable": ["timeout", "rate-limited"],
    "needs-user": ["missing-input", "ambiguous-request"],
    "external-down": ["connection-refused", "service-unavailable"],
}
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_PATTERN.sub("-", str(name or "").strip().lower()).strip("-")
    if not slug or not slug[0].isalpha():
        raise ValueError("Paket adı bir harfle başlamalı ve en az bir harf içermeli.")
    return slug


def _levels(levels) -> list[str]:
    if isinstance(levels, str):
        levels = [item for item in re.split(r"[,;]\s*", levels) if item]
    if not isinstance(levels, (list, tuple, set)):
        raise ValueError("Seviyeler bir liste veya virgüllü metin olmalı.")
    cleaned = [str(item).strip().lower() for item in levels if str(item).strip()]
    if not cleaned:
        raise ValueError("En az bir izin seviyesi gerekli.")
    unknown = sorted(set(cleaned) - LEVEL_SET)
    if unknown:
        raise ValueError(f"Bilinmeyen seviyeler: {', '.join(unknown)}.")
    return [level for level in LEVELS if level in set(cleaned)]


def _binding_levels(levels: list[str]) -> list[str]:
    safe = [level for level in levels if level != "dangerous"]
    return safe or ["read"]


def _binding_role(levels: list[str]) -> str:
    if "apply" in levels:
        return "implementer"
    if "draft" in levels:
        return "scheduler"
    return "reviewer"


def build_manifest(name: str, levels, *, description: str = "", adapter: str = "") -> dict:
    bridge_id = slugify(name)
    selected = _levels(levels)
    idempotency = (
        {"type": "keyed", "key": "idempotency_key"}
        if {"apply", "dangerous"} & set(selected)
        else {"type": "none"}
    )
    approval: dict[str, str] = {}
    if "apply" in selected:
        approval["apply"] = "approved"
    if "dangerous" in selected:
        approval["dangerous"] = "per_call_token"
    manifest = {
        "id": bridge_id,
        "version": "0.1.0",
        "description": description or f"{bridge_id} domain pack",
        "levels": selected,
        "idempotency": idempotency,
        "evidence": {
            "format": f"{bridge_id}-evidence",
            "fields": ["summary", "ok"],
        },
        "errors": json.loads(json.dumps(ERROR_SAMPLES)),
        "approval": approval,
    }
    if adapter:
        manifest["adapter"] = adapter
    return manifest


def render_evidence_module(manifest: dict) -> str:
    return f'''"""Sample evidence generator for {manifest["id"]}."""
from __future__ import annotations


def build_evidence(**details) -> dict:
    return {{
        "format": {manifest["evidence"]["format"]!r},
        "bridge": {manifest["id"]!r},
        "ok": True,
        "details": details,
    }}
'''


def render_persona_binding(manifest: dict) -> str:
    role = _binding_role(manifest["levels"])
    levels = _binding_levels(manifest["levels"])
    level_text = ", ".join(levels)
    return f'''---
role: {role}
bridges:
  {manifest["id"]}:
    levels: [{level_text}]
---

# {manifest["id"]} persona bağlaması

Bu blok bir persona frontmatter'ına eklenir. Rol tavanı motor kapısında ayrıca
denetlenir; `dangerous` seviyesi personaya yazılmaz, yalnız tekil onay token'ıyla
çağrılır.
'''


def _top_level(levels: list[str]) -> str:
    for level in ("dangerous", "apply", "draft", "read"):
        if level in levels:
            return level
    return levels[0]


def render_contract_test(manifest: dict) -> str:
    bridge_id = manifest["id"]
    levels = manifest["levels"]
    top_level = _top_level(levels)
    binding_role = _binding_role(levels)
    binding_levels = _binding_levels(levels)
    format_name = manifest["evidence"]["format"]

    dangerous_test = ""
    if "dangerous" in levels:
        dangerous_test = f'''
def test_dangerous_requires_single_use_token():
    gate = BridgeGate(_registry())
    with pytest.raises(PermissionError):
        gate.check("implementer", BRIDGE_ID, "dangerous", idempotency_key="smoke-key")
    first = gate.check("implementer", BRIDGE_ID, "dangerous",
                       idempotency_key="smoke-key",
                       approval_token="smoke-approval-token")
    assert first["approval_used"] is True
    assert first["ledger_key"].startswith(BRIDGE_ID + ":dangerous:")
    with pytest.raises(PermissionError):
        gate.check("implementer", BRIDGE_ID, "dangerous",
                   idempotency_key="smoke-key-2",
                   approval_token="smoke-approval-token")
'''

    if top_level == "dangerous":
        gate_call = '''    gate = BridgeGate(_registry())
    result = gate.check("implementer", BRIDGE_ID, "dangerous",
                        idempotency_key="smoke-key",
                        approval_token="smoke-approval-token")'''
    else:
        gate_call = f'''    gate = BridgeGate(_registry())
    result = gate.check(_persona(), BRIDGE_ID, {top_level!r},
                        idempotency_key="smoke-key",
                        approval_token="smoke-approval-token")'''

    return f'''"""Contract test for the {bridge_id} bridge pack."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_persona_manifest

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
EVIDENCE_PATH = Path(__file__).with_name("evidence.py")
BRIDGE_ID = {bridge_id!r}
PACK_LEVELS = {levels!r}
BINDING_ROLE = {binding_role!r}
BINDING_LEVELS = {binding_levels!r}
EVIDENCE_FORMAT = {format_name!r}


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _registry():
    registry = BridgeRegistry()
    registry.register(_manifest())
    return registry


def _persona():
    return {{"role": BINDING_ROLE, "bridges": {{BRIDGE_ID: {{"levels": BINDING_LEVELS}}}}}}

{dangerous_test}
def test_manifest_satisfies_contract():
    spec = _registry().get(BRIDGE_ID)
    assert spec["id"] == BRIDGE_ID
    assert set(spec["levels"]) == set(PACK_LEVELS)
    assert {{"retryable", "needs-user", "external-down"}} <= set(spec["errors"])
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
{gate_call}
    assert result["ledger_key"].startswith(BRIDGE_ID + ":" + {top_level!r} + ":")
'''


def write_pack(name: str, levels, out_dir, *, description: str = "",
               adapter: str = "") -> dict:
    manifest = build_manifest(name, levels, description=description, adapter=adapter)
    pack_dir = Path(out_dir) / manifest["id"]
    pack_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "id": manifest["id"],
        "dir": pack_dir,
        "manifest": pack_dir / "manifest.json",
        "evidence": pack_dir / "evidence.py",
        "contract_test": pack_dir / f"test_{manifest['id'].replace('-', '_')}_contract.py",
        "persona_binding": pack_dir / "persona_binding.md",
    }
    paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["evidence"].write_text(render_evidence_module(manifest), encoding="utf-8")
    paths["contract_test"].write_text(render_contract_test(manifest), encoding="utf-8")
    paths["persona_binding"].write_text(render_persona_binding(manifest), encoding="utf-8")
    return paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Yeni bir köprü/alan paketi iskeleti üretir.")
    parser.add_argument("--name", required=True, help="Paket adı (ör. office-local-files)")
    parser.add_argument("--levels", default="read",
                        help="Virgüllü izin seviyeleri: read,draft,apply,dangerous")
    parser.add_argument("--out", required=True, help="Paketlerin yazılacağı üst klasör")
    parser.add_argument("--description", default="", help="İnsan okur açıklama")
    parser.add_argument("--adapter", default="", help="Lazy adaptör hedefi: modul:fabrika")
    args = parser.parse_args(argv)
    paths = write_pack(args.name, args.levels, args.out,
                       description=args.description, adapter=args.adapter)
    print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
