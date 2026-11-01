"""Pack registry helpers: load the shipped reference packs into a gate/registry."""
from __future__ import annotations

import logging

from . import communication, github, office

log = logging.getLogger("manager.packs")

SHIPPED = {"office": office.register, "github": github.register,
           "communication": communication.register}
OFFICE_ID = office.BRIDGE_ID
GITHUB_ID = github.BRIDGE_ID
COMMUNICATION_ID = communication.BRIDGE_ID
PERSONAS = {
    office.BRIDGE_ID: {"manager": office.MANAGER_PERSONA,
                       "approver": office.APPROVER_PERSONA},
    github.BRIDGE_ID: {"manager": github.MANAGER_PERSONA,
                       "approver": github.APPROVER_PERSONA,
                       "coder": github.CODER_PERSONA},
    communication.BRIDGE_ID: {"manager": communication.MANAGER_PERSONA,
                              "approver": communication.APPROVER_PERSONA,
                              "proactive": communication.PROACTIVE_PERSONA},
}
# Levels a proactive background flow may ever use, per pack.
PROACTIVE_LEVELS = {communication.BRIDGE_ID: frozenset({"read"})}


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
    return [office.manifest(), github.manifest(), communication.manifest()]


def shipped_catalog() -> list[dict]:
    """Card data per pack: manifest facts plus persona grants."""
    return [{"bridge": manifest_value(module),
             "personas": PERSONAS.get(module.BRIDGE_ID, {})}
            for module in (office, github, communication)]


def manifest_value(module) -> dict:
    return module.manifest()


__all__ = ["register_shipped", "shipped_manifests", "shipped_catalog",
           "OFFICE_ID", "GITHUB_ID", "COMMUNICATION_ID", "PERSONAS",
           "PROACTIVE_LEVELS", "SHIPPED"]
