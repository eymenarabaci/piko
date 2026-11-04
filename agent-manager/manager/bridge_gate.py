"""Bridge call gate: the single place where a persona asks to use a bridge level.

The gate validates the role ceiling, the persona grant, the approval policy and
the idempotency contract, then returns a deterministic ledger key. It never
executes an adapter; the engine records the key in OperationLedger first and only
then may load the adapter.

Two entry points:

* ``check()`` — synchronous, in-process single-use tokens (unit tests, CLI).
* ``authorize()`` — asynchronous; when a durable token store (OperationLedger) is
  supplied, single-use approval is enforced across restarts.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

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
    def __init__(self, registry: BridgeRegistry | None = None, *,
                 token_store=None) -> None:
        self.registry = registry if registry is not None else DEFAULT_REGISTRY
        self.token_store = token_store
        self._used_tokens: set[str] = set()
        self._issued_tokens: dict[str, tuple] = {}

    def issue_approval(self, bridge: str, level: str, *, payload=None, ttl_seconds=600) -> str:
        if self.token_store is not None:
            raise ValueError("Kalıcı kapıda issue_approval_async kullanılmalı.")
        self._resolve_spec(bridge)
        if level not in {"apply", "dangerous"} or not 0 < ttl_seconds <= 3600:
            raise ValueError("Geçersiz onay kapsamı veya süresi.")
        token = f"{bridge}-{secrets.token_urlsafe(32)}"
        self._issued_tokens[token] = (bridge, level, _payload_hash(payload), time.time() + ttl_seconds)
        return token

    async def issue_approval_async(self, bridge: str, level: str, *, payload=None, ttl_seconds=600) -> str:
        self._resolve_spec(bridge)
        if self.token_store is None:
            return self.issue_approval(bridge, level, payload=payload, ttl_seconds=ttl_seconds)
        return await self.token_store.issue_approval(bridge=bridge, level=level, payload=payload, ttl_seconds=ttl_seconds)

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

    def _evaluate(self, persona, bridge, level: str, *, approval_token=None,
                  idempotency_key=None, payload=None, consume):
        """Validate one call. ``consume(token, bridge, level)`` returns True when
        the approval token is acceptable; it is called only after all
        authorization checks pass."""
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

        if level in {"apply", "dangerous"}:
            idempotency_key = str(idempotency_key or "").strip()
            if not idempotency_key:
                raise ValueError(f"'{level}' çağrısı bir idempotency anahtarı gerektirir.")
        else:
            idempotency_key = str(idempotency_key or "").strip()

        if level == "dangerous":
            approval_used = consume(approval_token, bridge_id, level)
        elif level == "apply":
            mode = str(spec["approval"].get("apply", "approved"))
            if mode == "per_call_token":
                approval_used = consume(approval_token, bridge_id, level)
            elif mode == "none":
                raise BridgeDenied(f"'{bridge_id}' için apply onay politikası kapalı.")

        payload_hash = _payload_hash(payload)
        material = f"{bridge_id}|{level}|{idempotency_key}|{payload_hash}"
        ledger_key = f"{bridge_id}:{level}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"
        return {
            "bridge": bridge_id,
            "level": level,
            "role": role,
            "idempotency_key": idempotency_key,
            "payload_hash": payload_hash,
            "ledger_key": ledger_key,
            "approval_used": approval_used,
        }

    def check(self, persona, bridge, level: str, *, approval_token=None,
              idempotency_key=None, payload=None) -> dict:
        """Synchronous authorization with in-process single-use tokens."""
        return self._evaluate(persona, bridge, level, approval_token=approval_token,
                              idempotency_key=idempotency_key, payload=payload,
                              consume=lambda token, bridge_id, level_name: self._consume_token(token, bridge_id, level_name, payload=payload))

    async def authorize(self, persona, bridge, level: str, *, approval_token=None,
                        idempotency_key=None, payload=None) -> dict:
        """Authorize one call, persisting single-use approval when a store exists."""
        if self.token_store is None:
            return self.check(persona, bridge, level, approval_token=approval_token,
                              idempotency_key=idempotency_key, payload=payload)

        # Dry evaluation first: a denied call must not consume the token.
        preview = self._evaluate(persona, bridge, level, approval_token="dry-run-token",
                       idempotency_key=idempotency_key, payload=payload,
                       consume=lambda *_: True)

        async def consume(token, bridge_id, level_name):
            token = str(token or "").strip()
            if not token:
                raise ApprovalRequired(
                    f"'{bridge_id}' köprüsünde '{level_name}' seviyesi tekil onay token'ı gerektirir."
                )
            accepted = await self.token_store.consume_approval(
                token, bridge=bridge_id, level=level_name, payload=payload,
                operation_key=preview["ledger_key"])
            if not accepted:
                raise ApprovalRequired("Onay geçersiz, kullanılmış, süresi dolmuş veya kapsamı değişmiş.")
            return True

        spec = self._resolve_spec(bridge)
        level_name = str(level or "").strip().lower()
        needs_token = level_name == "dangerous" or (
            level_name == "apply"
            and str(spec["approval"].get("apply", "approved")) == "per_call_token")
        if needs_token:
            await consume(approval_token, spec["id"], level_name)
        return self._evaluate(persona, bridge, level, approval_token=approval_token,
                              idempotency_key=idempotency_key, payload=payload,
                              consume=lambda *_: True)

    def _consume_token(self, approval_token, bridge_id: str, level: str, *, payload=None) -> bool:
        token = str(approval_token or "").strip()
        if not token:
            raise ApprovalRequired(
                f"'{bridge_id}' köprüsünde '{level}' seviyesi tekil onay token'ı gerektirir."
            )
        if token in self._used_tokens:
            raise ApprovalRequired(
                "Onay token'ı daha önce kullanıldı."
            )
        issued = self._issued_tokens.get(token)
        if issued is None or issued[:3] != (bridge_id, level, _payload_hash(payload)) or issued[3] <= time.time():
            raise ApprovalRequired("Onay üretilmemiş, süresi dolmuş veya işlem kapsamı değişmiş.")
        self._used_tokens.add(token)
        return True
