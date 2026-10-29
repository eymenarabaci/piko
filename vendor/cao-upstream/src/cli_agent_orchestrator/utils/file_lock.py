"""Cross-platform advisory file locks with an ``fcntl.flock``-like API.

POSIX uses kernel ``flock`` locks. Windows uses the CRT byte-range lock on
byte zero of the open file. The byte-range lock is exclusive even for a
requested shared lock, so Windows readers are serialized; this is deliberately
conservative and preserves mutual exclusion rather than silently dropping it.

Lock files may be empty. Windows byte-range locks can cover a range beyond EOF,
so this implementation does not need to write a sentinel byte or alter caller
file contents. The file pointer is restored after every operation because the
CRT API anchors its range at the current offset.
"""

from __future__ import annotations

import errno
import os
import time
from typing import Any

LOCK_SH = 0x01
LOCK_EX = 0x02
LOCK_NB = 0x04
LOCK_UN = 0x08
AVAILABLE = os.name in {"nt", "posix"}


def _fileno(file_or_fd: Any) -> int:
    if isinstance(file_or_fd, int):
        return file_or_fd
    fileno = getattr(file_or_fd, "fileno", None)
    if not callable(fileno):
        raise TypeError("file_lock.flock expects a file descriptor or file object")
    return int(fileno())


def flock(file_or_fd: Any, operation: int) -> None:
    """Acquire or release a process-shared advisory lock.

    The public flags intentionally match the familiar ``fcntl.flock`` API.
    Blocking acquisition waits until the lock is available; ``LOCK_NB`` raises
    ``BlockingIOError`` on contention.
    """
    fd = _fileno(file_or_fd)
    if operation & LOCK_UN:
        if operation & (LOCK_SH | LOCK_EX | LOCK_NB):
            raise ValueError("LOCK_UN cannot be combined with lock acquisition flags")
        _unlock(fd)
        return

    requested = operation & (LOCK_SH | LOCK_EX)
    if requested not in (LOCK_SH, LOCK_EX):
        raise ValueError("exactly one of LOCK_SH or LOCK_EX is required")
    nonblocking = bool(operation & LOCK_NB)

    if os.name == "nt":
        _windows_lock(fd, nonblocking=nonblocking)
        return

    import fcntl as _fcntl

    native = _fcntl.LOCK_EX if requested == LOCK_EX else _fcntl.LOCK_SH
    if nonblocking:
        native |= _fcntl.LOCK_NB
    _fcntl.flock(fd, native)


def _windows_lock(fd: int, *, nonblocking: bool) -> None:
    import msvcrt

    original_offset = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if not _is_contention(exc):
                    raise
                if nonblocking:
                    raise BlockingIOError(
                        errno.EAGAIN,
                        "another process holds the file lock",
                    ) from exc
                time.sleep(0.05)
    finally:
        os.lseek(fd, original_offset, os.SEEK_SET)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        original_offset = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.lseek(fd, original_offset, os.SEEK_SET)
        return

    import fcntl as _fcntl

    _fcntl.flock(fd, _fcntl.LOCK_UN)


def _is_contention(exc: OSError) -> bool:
    return exc.errno in {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", -1),
    }
