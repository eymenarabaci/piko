"""Readable evidence builder for the "veri" pack.

The rich builder lives in :mod:`bridge_packs.veri.adapter`; this module keeps the
small ``build_evidence(**details)`` shape used by the pack factory contract.
"""
from __future__ import annotations

BRIDGE_ID = "veri"
EVIDENCE_FORMAT = "veri-evidence"


def build_evidence(applied=None, **details) -> dict:
    if applied is not None:
        from bridge_packs.veri.adapter import build_evidence as _rich
        return _rich(applied)
    return {
        "format": EVIDENCE_FORMAT,
        "bridge": BRIDGE_ID,
        "ok": True,
        "details": details,
    }
