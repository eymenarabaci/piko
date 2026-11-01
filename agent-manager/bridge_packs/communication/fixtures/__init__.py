"""Offline mail + calendar fixture (see server.py)."""
from .server import DEFAULT_ACCOUNT, FixtureServer, tomorrow

__all__ = ["FixtureServer", "DEFAULT_ACCOUNT", "tomorrow"]
