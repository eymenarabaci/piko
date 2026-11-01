"""Sample evidence generator for medya.

The contract test imports only this module, so it must stay import-free of the
adapter. The real adapter builds a richer evidence object; this factory keeps the
contract shape (format + bridge + ok) and passes details through.
"""
from __future__ import annotations


def build_evidence(**details) -> dict:
    return {
        "format": 'medya-evidence',
        "bridge": 'medya',
        "ok": True,
        "details": details,
    }
