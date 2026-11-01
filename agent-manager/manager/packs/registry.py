"""Pack registry helpers: load the shipped reference packs into a gate/registry."""
from __future__ import annotations

import logging

from . import office

log = logging.getLogger("manager.packs")

SHIPPED = {"office": office.register}
OFFICE_ID = office.BRIDGE_ID


def register_shipped(registry) -> list[str]:
    """Register every shipped pack; a broken pack is skipped, never fatal."""
    registered = []
    for name, register in SHIPPED.items():
        try:
            register(registry)
            registered.append(name)
        except Exception:
            log.exception("Köprü paketi kaydedilemedi: %s", name)
    return registered


def shipped_manifests() -> list[dict]:
    return [office.manifest()]


__all__ = ["register_shipped", "shipped_manifests", "OFFICE_ID", "SHIPPED"]
