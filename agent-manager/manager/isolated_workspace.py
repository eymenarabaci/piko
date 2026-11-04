"""File-level isolated work copies and optimistic, rollback-protected merging.

The caller must hold the canonical workspace's exclusive lease during merge.
No Git operation, service or model invocation is performed here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid


EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "data", "work", "__pycache__",
    ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".nuxt",
    ".tox", ".turbo", "coverage", ".gradle", ".ssh", ".aws", ".azure", ".docker",
})
_SECRET = re.compile(r"(?i)(^\.env(?:$|\.|rc$)|^\.(?:npmrc|pypirc|netrc|git-credentials)$|credential|secret|private[-_]?key|"
                     r"service[-_]?account|^(?:id_rsa|id_ed25519)(?:$|\.)|"
                     r"^(?:auth|tokens?|passwords?)(?:$|\.)|\.(?:pem|key|pfx|p12|pyc)$)")


class WorkspaceConflict(RuntimeError):
    def __init__(self, conflicts):
        self.conflicts = sorted(set(conflicts))
        super().__init__("Canonical workspace conflicts: " + ", ".join(self.conflicts))


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _plain(path: Path) -> None:
    for part in [*reversed(path.parents), path]:
        if _is_link(part):
            raise ValueError(f"Linked paths are not allowed: {part}")


def _mkdir(path: Path) -> None:
    _plain(path)
    path.mkdir(parents=True, exist_ok=True)
    _plain(path)
    if not path.is_dir():
        raise ValueError(f"Expected directory: {path}")


def _relative(name: str) -> PurePosixPath:
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or "\\" in name or ":" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("Invalid workspace-relative path")
    if any(part.casefold() in EXCLUDED_DIRS for part in relative.parts) or _SECRET.search(relative.name):
        raise ValueError("Excluded workspace-relative path")
    return relative


def _read(path: Path) -> bytes:
    _plain(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Only regular files are supported: {path}")
    with path.open("rb") as handle:
        content = handle.read()
        inode = os.fstat(handle.fileno()).st_ino
    _plain(path)
    if path.lstat().st_ino != inode:
        raise ValueError(f"File changed while reading: {path}")
    return content


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _files(root: Path):
    _plain(root)
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        _plain(base)
        kept = []
        for name in sorted(dirs):
            if name.casefold() in EXCLUDED_DIRS:
                continue
            _plain(base / name)
            kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            if name.casefold() in EXCLUDED_DIRS or _SECRET.search(name):
                continue
            path = base / name
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            yield relative, path


def _snapshot(root: Path) -> dict[str, str]:
    return {name: _hash(_read(path)) for name, path in _files(root)}


def _atomic_bytes(path: Path, content: bytes) -> None:
    _plain(path)
    _mkdir(path.parent)
    temporary = path.with_name(f".{path.name}.piko-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _plain(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class IsolatedWorkspace:
    """Retry-stable isolated copy; call merge under an exclusive source lease."""

    def __init__(self, source: Path, storage: Path, identity: str):
        self.source = source
        self.storage = storage
        self.identity = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.container = storage / self.identity
        self.path = self.container / "workspace"
        self.metadata = self.container / "metadata.json"

    @classmethod
    def create(cls, source, storage, identity):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("A nonempty workspace identity is required")
        source, storage = Path(source).absolute(), Path(storage).absolute()
        _plain(source)
        _plain(storage)
        source, storage = source.resolve(), storage.resolve()
        if not source.is_dir():
            raise ValueError("Source workspace must be a directory")
        if source == storage or source.is_relative_to(storage) or storage.is_relative_to(source):
            raise ValueError("Storage and source trees must not overlap")
        _mkdir(storage)
        obj = cls(source, storage, identity)
        _plain(obj.container)
        if obj.container.exists():
            obj._load()
            _plain(obj.path)
            if not obj.path.is_dir():
                raise ValueError("Existing isolated workspace is incomplete; preserve it for inspection")
            return obj
        _mkdir(obj.path)
        baseline = {}
        # Failed creation is intentionally retained, never silently reused.
        for name, file in _files(source):
            content = _read(file)
            _atomic_bytes(obj.path / name, content)
            baseline[name] = _hash(content)
        obj._save({"version": 1, "source": str(source), "identity": obj.identity,
                   "baseline": baseline, "state": "ready"})
        return obj

    def _load(self):
        _plain(self.container)
        data = json.loads(_read(self.metadata).decode("utf-8"))
        if (data.get("version") != 1 or data.get("source") != str(self.source)
                or data.get("identity") != self.identity or not isinstance(data.get("baseline"), dict)):
            raise ValueError("Invalid isolated workspace metadata")
        for name, digest in data["baseline"].items():
            _relative(name)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid baseline content hash")
        return data

    def _save(self, data):
        _atomic_bytes(self.metadata, json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def merge(self):
        """Preflight every changed file, then apply with backups and rollback."""
        data = self._load()
        baseline = data["baseline"]
        branch = _snapshot(self.path)
        current = _snapshot(self.source)
        changes = sorted(name for name in set(baseline) | set(branch)
                         if baseline.get(name) != branch.get(name))
        conflicts = []
        writes, deletes, unchanged = {}, [], []
        for name in changes:
            _relative(name)
            target = self.source / name
            _plain(target)
            blocked_parent = any(parent.exists() and not parent.is_dir()
                                 for parent in target.parents if parent != self.source.parent)
            desired, actual = branch.get(name), current.get(name)
            if blocked_parent or (target.exists() and not target.is_file()):
                conflicts.append(name)
            elif actual == desired:
                unchanged.append(name)
            elif actual != baseline.get(name):
                conflicts.append(name)
            elif desired is None:
                deletes.append(name)
            else:
                content = _read(self.path / name)
                if _hash(content) != desired:
                    raise ValueError("Isolated workspace changed during preflight")
                writes[name] = content
        if conflicts:
            data.update(state="conflict", conflicts=sorted(set(conflicts)))
            self._save(data)
            raise WorkspaceConflict(conflicts)

        backup = self.container / ("backup-" + uuid.uuid4().hex)
        _mkdir(backup)
        originals = {}
        applied = []
        created_dirs = []
        try:
            # Snapshot all touched canonical bytes before the first mutation.
            for name in sorted([*writes, *deletes]):
                target = self.source / name
                original = _read(target) if target.exists() else None
                if (None if original is None else _hash(original)) != current.get(name):
                    raise WorkspaceConflict([name])
                originals[name] = original is not None
                if original is not None:
                    _atomic_bytes(backup / name, original)
            for name in sorted([*writes, *deletes]):
                target = self.source / name
                _plain(target)
                observed = _hash(_read(target)) if target.exists() else None
                if observed != current.get(name):
                    raise WorkspaceConflict([name])
                if name in writes:
                    parents = []
                    parent = target.parent
                    while not parent.exists():
                        parents.append(parent)
                        parent = parent.parent
                    created_dirs.extend(reversed(parents))
                    _atomic_bytes(target, writes[name])
                else:
                    target.unlink()
                applied.append(name)
            data.update(state="merged", merged_snapshot=branch, conflicts=[])
            self._save(data)
        except BaseException:
            rollback_errors = []
            for name in reversed(applied):
                target = self.source / name
                try:
                    _plain(target)
                    # Another actor may edit a file after our apply but before
                    # rollback. Restore only the exact bytes we wrote (or the
                    # absence left by our deletion), never that actor's change.
                    expected = _hash(writes[name]) if name in writes else None
                    observed = _hash(_read(target)) if target.exists() else None
                    if observed != expected:
                        rollback_errors.append(name)
                        continue
                    if originals[name]:
                        _atomic_bytes(target, _read(backup / name))
                    elif target.exists():
                        target.unlink()
                except Exception:
                    rollback_errors.append(name)
            for directory in reversed(created_dirs):
                try:
                    _plain(directory)
                    directory.rmdir()
                except OSError:
                    pass
            data.update(state="rollback_failed" if rollback_errors else "failed",
                        rollback_errors=rollback_errors)
            try:
                self._save(data)
            except Exception:
                pass
            raise
        return {"changed": sorted(writes), "deleted": sorted(deletes), "unchanged": unchanged}

    def cleanup(self) -> bool:
        """Remove only this successful, unchanged copy; retain failed evidence."""
        data = self._load()
        if data.get("state") != "merged" or _snapshot(self.path) != data.get("merged_snapshot"):
            return False
        _plain(self.storage)
        _plain(self.container)
        if (self.container.name != self.identity or self.container.parent.resolve() != self.storage.resolve()
                or self.path.parent != self.container):
            raise ValueError("Unsafe isolated workspace cleanup target")
        # Check excluded subtrees too before recursive cleanup, without following links.
        for directory, dirs, files in os.walk(self.container, followlinks=False):
            for name in [*dirs, *files]:
                _plain(Path(directory) / name)
        shutil.rmtree(self.container)
        return True
