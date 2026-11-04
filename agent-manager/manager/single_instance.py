"""Cross-process single-instance guard for the unattended driver.

Only one driver loop may tick against a data directory. Without this guard the
scheduled watchdog start, a second watchdog start after a missed heartbeat and
the in-process application driver can all run at once. Each of them resumes the
same ``active`` work, starts the same ``open`` item and competes for slots,
which duplicates runs, creates ambiguous operations and corrupts the resource
ledger.

The lock is an OS byte-range lock held for the process lifetime, so it is
released automatically even when the process is killed. It is also reentrant
inside one process: the application may hand the lock to its in-process driver
without the driver failing to acquire it again.
"""
from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised on POSIX runners
    import fcntl


_HELD: dict[str, dict] = {}


class InstanceLock:
    """Advisory exclusive lock on ``path`` shared by every driver in a process."""

    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self._owned = False

    def acquire(self) -> bool:
        """Return True when this process owns the lock, False when another does."""
        if self._owned:
            return True
        entry = _HELD.get(self.path)
        if entry is not None:
            entry["count"] += 1
            self._owned = True
            return True
        handle = self._open_and_lock()
        if handle is None:
            return False
        _HELD[self.path] = {"handle": handle, "count": 1, "pid": os.getpid()}
        self._owned = True
        return True

    def _open_and_lock(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if handle.tell() == 0:
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
        except OSError:
            pass
        return handle

    def release(self) -> None:
        if not self._owned:
            return
        self._owned = False
        entry = _HELD.get(self.path)
        if entry is None:
            return
        entry["count"] -= 1
        if entry["count"] > 0:
            return
        _HELD.pop(self.path, None)
        handle = entry["handle"]
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Başka bir sürücü zaten çalışıyor: {self.path}")
        return self

    def __exit__(self, *_exc):
        self.release()
        return False
