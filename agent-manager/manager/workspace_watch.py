"""Cheap workspace change detection: proof that a CLI step actually did something.

The CAO terminal sometimes finishes a real edit and then hands back the prompt
instead of a summary. Discarding such a step throws away real work and re-runs
it forever, so the engine snapshots the workspace around a step and accepts the
step when the files prove the change happened.
"""
from __future__ import annotations

import os

SKIP_DIRECTORIES = frozenset({
    "node_modules", ".git", ".next", ".nuxt", "dist", "build", "out", ".venv", "venv",
    "__pycache__", ".cache", ".pytest_cache", "coverage", ".turbo", ".parcel-cache",
    "vendor", ".idea", ".vscode", ".piko",
})
_SKIP_SUFFIXES = frozenset({
    ".lock", ".log", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".pyc", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".ico", ".mp4", ".woff", ".woff2", ".ttf", ".map",
})
MAX_FILES = 6000


def fingerprint(root: str | os.PathLike[str]) -> dict[str, tuple[int, int]]:
    """Map every tracked file under `root` to (mtime_ns, size). Never raises."""
    base = str(root or "")
    if not base or not os.path.isdir(base):
        return {}
    seen: dict[str, tuple[int, int]] = {}
    for current, directories, files in os.walk(base):
        directories[:] = [name for name in directories
                          if name.lower() not in SKIP_DIRECTORIES and not name.startswith(".next")]
        for name in files:
            if os.path.splitext(name)[1].lower() in _SKIP_SUFFIXES:
                continue
            path = os.path.join(current, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            seen[os.path.relpath(path, base).lower()] = (stat.st_mtime_ns, stat.st_size)
            if len(seen) >= MAX_FILES:
                return seen
    return seen


def changes(before: dict[str, tuple[int, int]], root: str | os.PathLike[str]) -> dict:
    """Diff the current workspace against `before`; keys are sorted relative paths."""
    after = fingerprint(root)
    if not before:
        return {"changed": [], "added": [], "removed": [], "count": 0}
    changed = sorted(key for key, value in after.items()
                     if key in before and before[key] != value)
    added = sorted(key for key in after if key not in before)
    removed = sorted(key for key in before if key not in after)
    return {"changed": changed, "added": added, "removed": removed,
            "count": len(changed) + len(added) + len(removed)}


def summarize(diff: dict, limit: int = 12) -> str:
    parts = []
    for label, key in (("degisen", "changed"), ("yeni", "added"), ("silinen", "removed")):
        items = diff.get(key) or []
        if items:
            shown = ", ".join(items[:limit])
            more = f" (+{len(items) - limit})" if len(items) > limit else ""
            parts.append(f"{label}: {shown}{more}")
    return "; ".join(parts) or "degisiklik yok"
