"""Sample evidence generator for communication."""
from __future__ import annotations


def build_evidence(**details) -> dict:
    return {
        "format": 'communication-evidence',
        "bridge": 'communication',
        "ok": True,
        "details": details,
    }
