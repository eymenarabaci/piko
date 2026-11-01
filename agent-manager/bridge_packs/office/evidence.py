"""Sample evidence generator for office."""
from __future__ import annotations


def build_evidence(**details) -> dict:
    return {
        "format": 'office-evidence',
        "bridge": 'office',
        "ok": True,
        "details": details,
    }
