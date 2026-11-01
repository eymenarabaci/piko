"""Pack registry helpers: load the shipped reference packs into a gate/registry."""
from __future__ import annotations

import logging

from . import github, office

log = logging.getLogger("manager.packs")

SHIPPED = {"office": office.register, "github": github.register}
OFFICE_ID = office.BRIDGE_ID
GITHUB_ID = github.BRIDGE_ID
PERSONAS = {
    office.BRIDGE_ID: {"manager": office.MANAGER_PERSONA,
                       "approver": office.APPROVER_PERSONA},
    github.BRIDGE_ID: {"manager": github.MANAGER_PERSONA,
                       "approver": github.APPROVER_PERSONA,
                       "coder": github.CODER_PERSONA},
}


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
    """Every pack manifest, read from disk — the developer card is manifest-driven."""
    return [office.manifest(), github.manifest()]


def shipped_catalog() -> list[dict]:
    """Card data per pack: manifest facts plus persona grants and last delivery."""
    return [{"bridge": office.manifest(),
             "personas": PERSONAS.get(office.BRIDGE_ID, {})},
            {"bridge": github.manifest(),
             "personas": PERSONAS.get(github.BRIDGE_ID, {})}]


__all__ = ["register_shipped", "shipped_manifests", "shipped_catalog",
           "OFFICE_ID", "GITHUB_ID", "PERSONAS", "SHIPPED"]
