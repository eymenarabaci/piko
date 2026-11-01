"""Sample evidence generator for github."""
from __future__ import annotations


def build_evidence(**details) -> dict:
    return {
        "format": 'github-evidence',
        "bridge": 'github',
        "ok": True,
        "details": details,
    }
