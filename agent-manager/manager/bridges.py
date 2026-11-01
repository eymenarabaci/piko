"""Bridge contract schema, persona role ceilings and a lazy bridge registry.

A bridge is a contract, not an implementation. Every domain pack must pass the
six mandatory clauses before the registry accepts it: permission levels,
idempotency type, evidence format, role ceiling, error taxonomy and approval
policy. Adapters are imported only when a bridge is actually invoked, so an
unused pack costs zero runtime and zero tokens.
"""
from __future__ import annotations

import importlib
import re
from copy import deepcopy

from .operations import ROLE_TOOL_PERMISSIONS

LEVELS = ("read", "draft", "apply", "dangerous")
LEVEL_SET = frozenset(LEVELS)
IDEMPOTENCY_TYPES = frozenset({"none", "keyed", "content-hash", "natural-key"})
ERROR_CLASSES = ("retryable", "needs-user", "external-down")
ERROR_CLASS_SET = frozenset(ERROR_CLASSES)
APPROVAL_MODES = frozenset({"approved", "per_call_token", "none"})
REQUIRED_FIELDS = ("id", "levels", "idempotency", "evidence", "errors", "approval")
_ROLE_LEVELS = ("read", "draft", "apply")
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def _ordered_levels(value, field: str = "levels") -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError(f"'{field}' bir liste olmalı.")
    levels = [str(item).strip().lower() for item in value if str(item).strip()]
    if not levels:
        raise ValueError(f"'{field}' en az bir izin seviyesi içermeli.")
    unknown = sorted(set(levels) - LEVEL_SET)
    if unknown:
        raise ValueError(f"Bilinmeyen izin seviyeleri: {', '.join(unknown)}.")
    return [level for level in LEVELS if level in set(levels)]


def _normalize_bridge_grants(bridges) -> dict[str, list[str]] | None:
    """Accept persona grants as a mapping {bridge: levels} or a list of entries."""
    if bridges is None:
        return None
    grants: dict[str, list[str]] = {}
    if isinstance(bridges, dict):
        for key, value in bridges.items():
            bridge_id = str(key or "").strip()
            if not bridge_id:
                raise ValueError("Persona köprü kimliği boş olamaz.")
            if isinstance(value, dict):
                if "levels" not in value:
                    raise ValueError(f"'{bridge_id}' için 'levels' gerekli.")
                levels = _ordered_levels(value["levels"], f"{bridge_id}.levels")
            else:
                levels = _ordered_levels(value, f"{bridge_id}.levels")
            grants[bridge_id] = levels
        return grants
    if isinstance(bridges, (list, tuple)):
        for entry in bridges:
            if not isinstance(entry, dict):
                raise ValueError("Persona köprü listesi nesneler içermeli.")
            bridge_id = str(entry.get("id") or entry.get("name") or "").strip()
            if not bridge_id or "levels" not in entry:
                raise ValueError("Her persona köprü kaydı 'id' ve 'levels' taşımalı.")
            grants[bridge_id] = _ordered_levels(entry["levels"], f"{bridge_id}.levels")
        return grants
    raise ValueError("'bridges' bir nesne veya liste olmalı.")


def role_level_ceiling(role: str) -> frozenset[str]:
    """Return the bridge levels a role may ever use. 'dangerous' is never a role right."""
    role = str(role or "").strip()
    if role not in ROLE_TOOL_PERMISSIONS:
        raise PermissionError(f"Bilinmeyen rol: '{role}'.")
    permissions = ROLE_TOOL_PERMISSIONS[role]
    ceiling = {level for level in _ROLE_LEVELS if level in permissions}
    if "dangerous" in permissions:
        ceiling.add("dangerous")
    return frozenset(ceiling)


def validate_bridge_spec(spec: dict) -> dict:
    """Validate the six-clause bridge contract and return a normalized copy."""
    if not isinstance(spec, dict):
        raise ValueError("Köprü sözleşmesi bir nesne olmalı.")
    missing = [field for field in REQUIRED_FIELDS if field not in spec]
    if missing:
        raise ValueError(f"Köprü sözleşmesinde zorunlu alanlar eksik: {', '.join(missing)}.")

    bridge_id = str(spec["id"] or "").strip()
    if not bridge_id or not _ID_PATTERN.fullmatch(bridge_id):
        raise ValueError("Köprü kimliği küçük harf, rakam ve . _ - içermeli.")

    levels = _ordered_levels(spec["levels"])

    idempotency = spec["idempotency"]
    if not isinstance(idempotency, dict):
        raise ValueError("'idempotency' bir nesne olmalı.")
    idempotency = dict(idempotency)
    idempotency_type = str(idempotency.get("type") or "").strip()
    if idempotency_type not in IDEMPOTENCY_TYPES:
        raise ValueError(f"Geçersiz idempotency türü: '{idempotency_type}'.")
    idempotency["type"] = idempotency_type
    if idempotency_type != "none":
        idempotency.setdefault("key", "idempotency_key")
    if {"apply", "dangerous"} & set(levels) and idempotency_type == "none":
        raise ValueError("apply/dangerous seviyeleri anahtarlı idempotency gerektirir.")

    evidence = spec["evidence"]
    if not isinstance(evidence, dict) or not str(evidence.get("format") or "").strip():
        raise ValueError("'evidence.format' zorunlu.")
    evidence = dict(evidence)
    evidence["format"] = str(evidence["format"]).strip()
    if "fields" in evidence:
        if not isinstance(evidence["fields"], (list, tuple)):
            raise ValueError("'evidence.fields' bir liste olmalı.")
        evidence["fields"] = [str(item).strip() for item in evidence["fields"] if str(item).strip()]

    errors = spec["errors"]
    if not isinstance(errors, dict):
        raise ValueError("'errors' bir nesne olmalı.")
    normalized_errors: dict[str, list[str]] = {}
    for error_class in ERROR_CLASSES:
        if error_class not in errors:
            raise ValueError(f"Hata taksonomisinde '{error_class}' sınıfı eksik.")
        if not isinstance(errors[error_class], (list, tuple)):
            raise ValueError(f"'{error_class}' hata sınıfı liste olmalı.")
        normalized_errors[error_class] = [
            str(item).strip() for item in errors[error_class] if str(item).strip()
        ]

    approval = spec["approval"]
    if not isinstance(approval, dict):
        raise ValueError("'approval' bir nesne olmalı.")
    approval = dict(approval)
    for key, value in approval.items():
        if key not in LEVEL_SET:
            raise ValueError(f"Onay politikasında geçersiz seviye: '{key}'.")
        if str(value) not in APPROVAL_MODES:
            raise ValueError(f"Geçersiz onay modu: '{value}'.")
        approval[key] = str(value)
    if "apply" in levels:
        approval.setdefault("apply", "approved")
    if "dangerous" in levels and approval.get("dangerous") != "per_call_token":
        raise ValueError("'dangerous' seviyesi her çağrıda tekil onay token'ı gerektirir.")

    adapter = spec.get("adapter", "") or ""
    if adapter:
        if not isinstance(adapter, str) or ":" not in adapter:
            raise ValueError("'adapter' 'modul:fabrika' biçiminde olmalı.")

    return {
        "id": bridge_id,
        "version": str(spec.get("version") or "0.1.0"),
        "description": str(spec.get("description") or ""),
        "levels": levels,
        "idempotency": idempotency,
        "evidence": evidence,
        "errors": normalized_errors,
        "approval": approval,
        "adapter": adapter,
    }


def validate_persona_manifest(persona_manifest: dict, role: str) -> dict:
    """Validate persona bridge grants against the role ceiling.

    A persona may never request a level above its role. 'dangerous' is not a
    role right at all; it is granted per call through an approval token.
    """
    if not isinstance(persona_manifest, dict):
        raise ValueError("Persona manifesti bir nesne olmalı.")
    role = str(role or persona_manifest.get("role") or "").strip()
    if not role:
        raise ValueError("Persona manifesti bir rol taşımalı.")
    ceiling = role_level_ceiling(role)
    grants = _normalize_bridge_grants(persona_manifest.get("bridges")) or {}

    for bridge_id, levels in grants.items():
        if not _ID_PATTERN.fullmatch(bridge_id):
            raise ValueError(f"Geçersiz köprü kimliği: '{bridge_id}'.")
        over = sorted(set(levels) - set(ceiling))
        if over:
            raise PermissionError(
                f"{role} rolü '{bridge_id}' köprüsünde {over} seviyesini kullanamaz; "
                f"rol tavanı {sorted(ceiling)}."
            )

    return {
        "role": role,
        "bridges": {bridge_id: list(levels) for bridge_id, levels in grants.items()},
    }


class BridgeRegistry:
    """Holds contract metadata eagerly and imports adapters only on first use."""

    def __init__(self) -> None:
        self._specs: dict[str, dict] = {}
        self._adapters: dict[str, object] = {}

    def register(self, spec: dict, *, replace: bool = False) -> dict:
        normalized = validate_bridge_spec(spec)
        bridge_id = normalized["id"]
        if bridge_id in self._specs and not replace:
            raise ValueError(f"Köprü zaten kayıtlı: '{bridge_id}'.")
        self._specs[bridge_id] = deepcopy(normalized)
        if replace:
            self._adapters.pop(bridge_id, None)
        return deepcopy(normalized)

    def register_many(self, specs, *, replace: bool = False) -> list[dict]:
        return [self.register(spec, replace=replace) for spec in specs]

    def unregister(self, bridge_id: str) -> None:
        bridge_id = str(bridge_id or "").strip()
        if bridge_id not in self._specs:
            raise KeyError(f"Köprü kayıtlı değil: '{bridge_id}'.")
        del self._specs[bridge_id]
        self._adapters.pop(bridge_id, None)

    def get(self, bridge_id: str) -> dict:
        bridge_id = str(bridge_id or "").strip()
        try:
            return deepcopy(self._specs[bridge_id])
        except KeyError as exc:
            raise KeyError(f"Köprü kayıtlı değil: '{bridge_id}'.") from exc

    def list(self) -> list[dict]:
        return [deepcopy(spec) for spec in self._specs.values()]

    def catalog(self) -> list[dict]:
        return [
            {
                "id": spec["id"],
                "version": spec["version"],
                "levels": list(spec["levels"]),
                "adapter": bool(spec.get("adapter")),
                "loaded": bridge_id in self._adapters,
            }
            for bridge_id, spec in self._specs.items()
        ]

    def is_loaded(self, bridge_id: str) -> bool:
        return str(bridge_id or "").strip() in self._adapters

    def load_adapter(self, bridge_id: str):
        """Import the adapter named by 'module:attribute' the first time it is needed."""
        bridge_id = str(bridge_id or "").strip()
        spec = self._specs.get(bridge_id)
        if spec is None:
            raise KeyError(f"Köprü kayıtlı değil: '{bridge_id}'.")
        target = spec.get("adapter") or ""
        if not target:
            return None
        if bridge_id in self._adapters:
            return self._adapters[bridge_id]
        module_name, _, attribute = target.partition(":")
        module = importlib.import_module(module_name)
        try:
            adapter = getattr(module, attribute)
        except AttributeError as exc:
            raise ValueError(
                f"'{target}' adaptörü '{module_name}' modülünde bulunamadı."
            ) from exc
        self._adapters[bridge_id] = adapter
        return adapter

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, bridge_id: object) -> bool:
        return str(bridge_id or "").strip() in self._specs

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._specs)


DEFAULT_REGISTRY = BridgeRegistry()
