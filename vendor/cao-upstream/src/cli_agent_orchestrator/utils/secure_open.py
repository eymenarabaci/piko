"""Open filesystem objects without following leaf symlinks/reparse points.

POSIX delegates to ``O_NOFOLLOW`` and supports ``dir_fd``. On Windows,
``CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`` opens the leaf itself, then the
handle is rejected if its attributes mark it as any kind of reparse point.
Windows handle-relative opens are not implemented: calls supplying ``dir_fd``
fail closed instead of falling back to a race-prone path check. This means the
vault's component-by-component descriptor traversal remains unavailable on
Windows until a native ``NtCreateFile(RootDirectory=...)`` implementation is
added. Mode bits are advisory on Windows; the user's ACL remains authoritative.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any


def open_no_follow(path: Any, flags: int, mode: int = 0o666, *, dir_fd: int | None = None) -> int:
    """Open a path while refusing a symlink/reparse point at its final component.

    On POSIX this preserves the caller's flags and adds ``O_NOFOLLOW``. On
    Windows the supported subset is read/write/create/exclusive/truncate/
    append/nonblock/directory/binary/close-on-exec. Unknown flags and
    handle-relative opens fail closed.
    """
    if os.name != "nt":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise OSError(errno.ENOTSUP, "O_NOFOLLOW is unavailable on this platform")
        if dir_fd is None:
            return os.open(path, flags | nofollow, mode)
        return os.open(path, flags | nofollow, mode, dir_fd=dir_fd)
    return _open_windows(path, flags, mode, dir_fd=dir_fd)


def open_directory_no_follow(path: Any) -> int:
    """Open an existing directory and reject a leaf reparse point."""
    directory = getattr(os, "O_DIRECTORY", 0)
    if os.name == "nt":
        return _open_windows(path, os.O_RDONLY | directory, 0o666, dir_fd=None, directory=True)
    return open_no_follow(path, os.O_RDONLY | directory | getattr(os, "O_NOFOLLOW", 0))


def _open_windows(path: Any, flags: int, mode: int, *, dir_fd: int | None, directory: bool = False) -> int:
    del mode  # Windows filesystem ACLs, not POSIX mode bits, govern access.
    if dir_fd is not None:
        raise OSError(
            getattr(errno, "ENOTSUP", errno.EINVAL),
            "secure handle-relative opens are not implemented on Windows",
        )

    import ctypes
    from ctypes import wintypes
    import msvcrt

    path_text = os.fsdecode(os.fspath(path))
    if not path_text or "\x00" in path_text:
        raise ValueError("path must be a non-empty filesystem path")
    if path_text.startswith("\\\\.\\") or path_text.upper().startswith("\\\\?\\GLOBALROOT"):
        raise OSError(errno.EACCES, "device namespace paths are not accepted")

    access_flags = os.O_RDONLY | os.O_WRONLY | os.O_RDWR
    create_flags = os.O_CREAT | os.O_EXCL | os.O_TRUNC | os.O_APPEND
    safe_flags = (
        access_flags
        | create_flags
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    unsupported = flags & ~safe_flags
    if unsupported:
        raise OSError(errno.EINVAL, f"unsupported secure-open flags: 0x{unsupported:x}")

    access = flags & access_flags
    if access not in (os.O_RDONLY, os.O_WRONLY, os.O_RDWR):
        raise OSError(errno.EINVAL, "exactly one read/write access mode is required")

    if access == os.O_RDONLY:
        desired_access = 0x80000000  # GENERIC_READ
    elif access == os.O_WRONLY:
        desired_access = 0x40000000  # GENERIC_WRITE
    else:
        desired_access = 0xC0000000  # GENERIC_READ | GENERIC_WRITE

    creating = bool(flags & os.O_CREAT)
    exclusive = bool(flags & os.O_EXCL)
    truncating = bool(flags & os.O_TRUNC)
    if exclusive and not creating:
        raise OSError(errno.EINVAL, "O_EXCL requires O_CREAT on Windows")
    if truncating and access == os.O_RDONLY:
        raise OSError(errno.EINVAL, "O_TRUNC requires write access on Windows")
    if directory and (creating or exclusive or truncating):
        raise OSError(errno.EINVAL, "directory opens cannot create or truncate on Windows")
    if creating and exclusive:
        disposition = 1  # CREATE_NEW
    elif creating and truncating:
        disposition = 2  # CREATE_ALWAYS
    elif creating:
        disposition = 4  # OPEN_ALWAYS
    elif truncating:
        disposition = 5  # TRUNCATE_EXISTING
    else:
        disposition = 3  # OPEN_EXISTING

    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", FILETIME),
            ("ftLastAccessTime", FILETIME),
            ("ftLastWriteTime", FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    attributes = FILE_FLAG_OPEN_REPARSE_POINT
    if directory or flags & getattr(os, "O_DIRECTORY", 0):
        attributes |= FILE_FLAG_BACKUP_SEMANTICS
    handle = kernel32.CreateFileW(
        path_text,
        desired_access,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        disposition,
        attributes | FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        info = BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "refusing to open a Windows reparse point", path_text)
        if (directory or flags & getattr(os, "O_DIRECTORY", 0)) and not (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY):
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", path_text)

        fd_flags = 0
        if flags & os.O_WRONLY:
            fd_flags |= os.O_WRONLY
        elif flags & os.O_RDWR:
            fd_flags |= os.O_RDWR
        else:
            fd_flags |= os.O_RDONLY
        if flags & os.O_APPEND:
            fd_flags |= os.O_APPEND
        fd_flags |= getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOINHERIT"):
            fd_flags |= os.O_NOINHERIT
        fd = msvcrt.open_osfhandle(int(handle), fd_flags)
        handle = None
        return fd
    finally:
        if handle not in (None, 0):
            kernel32.CloseHandle(handle)
