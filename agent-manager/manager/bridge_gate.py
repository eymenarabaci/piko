"""Bridge call gate: the single place where a persona asks to use a bridge level.

The gate does not perform side effects yet. It validates the role ceiling,
the persona grant, the approval policy and the idempotency contract, then
returns a deterministic ledger key that a later task will bind to
OperationLedger and the persisted approval store.
"""
from __future__ import annotations

import hashlib
import json

from .bridges import (
    DEFAULT_REGISTRY,
    LEVEL_SET,
    BridgeRegistry,
    _normalize_bridge_grants,
    role_level_ceiling,
    validate_bridge_spec,
)


class BridgeDenied(PermissionError):
    """The persona/role/bridge combination is not allowed."""


class ApprovalRequired(BridgeDenied):
    """A token-gated level was requested without a usable approval token."""


def _payload_hash(payload) -> str:
    material = json.dumps(payload if payload is not None else {}, ensure_ascii=False,
                          sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class BridgeGate:
    def __init__(self, registry: BridgeRegistry | None = None) -> None:
        self.registry = registry if registry is not None else DEFAULT_REGISTRY
        self._used_tokens: set[str] = set()

    def _resolve_spec(self, bridge) -> dict:
        if isinstance(bridge, str):
            return self.registry.get(bridge)
        if isinstance(bridge, dict):
            return validate_bridge_spec(bridge)
        raise ValueError("Köprü bir kimlik veya sözleşme nesnesi olmalı.")

    @staticmethod
    def _resolve_persona(persona) -> tuple[str, dict[str, list[str]] | None]:
        if isinstance(persona, str):
            role = persona.strip()
            if not role:
                raise ValueError("Persona rolü boş olamaz.")
            return role, None
        if not isinstance(persona, dict):
            raise ValueError("Persona bir rol adı veya manifest nesnesi olmalı.")
        role = str(persona.get("role") or persona.get("persona") or "").strip()
        if not role:
            raise ValueError("Persona manifesti bir rol taşımalı.")
        return role, _normalize_bridge_grants(persona.get("bridges"))

    def check(self, persona, bridge, level: str, *, approval_token=None,
              idempotency_key=None, payload=None) -> dict:
        """Authorize one bridge call and return its deterministic ledger key."""
        spec = self._resolve_spec(bridge)
        role, grants = self._resolve_persona(persona)
        level = str(level or "").strip().lower()

        if level not in LEVEL_SET:
            raise ValueError(f"Bilinmeyen izin seviyesi: '{level}'.")
        bridge_id = spec["id"]
        if level not in spec["levels"]:
            raise BridgeDenied(f"'{bridge_id}' köprüsü '{level}' seviyesini sunmuyor.")

        ceiling = role_level_ceiling(role)
        if grants is not None:
            if bridge_id not in grants:
                raise BridgeDenied(f"{role} personasına '{bridge_id}' köprüsü bağlı değil.")
            if level not in grants[bridge_id]:
                raise BridgeDenied(
                    f"{role} personası '{bridge_id}' köprüsünde '{level}' seviyesine sahip değil."
                )

        if level != "dangerous" and level not in ceiling:
            raise BridgeDenied(
                f"{role} rolü '{level}' seviyesini kullanamaz; rol tavanı {sorted(ceiling)}."
            )

        approval_used = False
        if level == "dangerous":
            approval_used = self._consume_token(approval_token, bridge_id, level)
            # INTEGRATION: the approval token is currently kept in memory; it will be
            # verified against the persisted single-use approval store in a later task.
        elif level == "apply":
            mode = str(spec["approval"].get("apply", "approved"))
            if mode == "per_call_token":
                approval_used = self._consume_token(approval_token, bridge_id, level)
            elif mode == "none":
                raise BridgeDenied(f"'{bridge_id}' için apply onay politikası kapalı.")
            # INTEGRATION: apply calls will be written to OperationLedger.begin(...)
            # with the ledger_key returned below once the ledger is wired in.

        if level in {"apply", "dangerous"}:
            idempotency_key = str(idempotency_key or "").strip()
            if not idempotency_key:
                raise ValueError(f"'{level}' çağrısı bir idempotency anahtarı gerektirir.")
        else:
            idempotency_key = str(idempotency_key or "").strip()

        payload_hash = _payload_hash(payload)
        material = f"{bridge_id}|{level}|{idempotency_key}|{payload_hash}"
        ledger_key = f"{bridge_id}:{level}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"

        # INTEGRATION: bridge_gate does not execute adapters; the driver will call
        # registry.load_adapter(bridge_id) only after this key is durably recorded.
        return {
            "bridge": bridge_id,
            "level": level,
            "role": role,
            "idempotency_key": idempotency_key,
            "payload_hash": payload_hash,
            "ledger_key": ledger_key,
            "approval_used": approval_used,
        }

    def _consume_token(self, approval_token, bridge_id: str, level: str) -> bool:
        token = str(approval_token or "").strip()
        if not token:
            raise ApprovalRequired(
                f"'{bridge_id}' köprüsünde '{level}' seviyesi tekil onay token'ı gerektirir."
            )
        if token in self._used_tokens:
            raise ApprovalRequired(
                f"Onay token'ı daha önce kullanıldı: '{token}'."
            )
        self._used_tokens.add(token)
        return True
