"""Native Windows terminal backend using pywinpty/ConPTY.

This is the first native-Windows backend seam for CAO. A terminal is one
Windows pseudo-console process; CAO sessions/windows are lightweight names
that group those processes. Output is captured by one reader thread per PTY and
kept in bounded buffers. The additional ``subscribe_output`` API lets the HTTP
WebSocket layer bridge the *same* PTY without attaching a second process.

Limitations while the surrounding CAO services are being ported:

* ``api.main`` currently opens POSIX PTYs directly for WebSocket attachment and
  the terminal service still creates FIFO readers. Those callers must be moved
  to ``subscribe_output``/``send_raw`` before this backend is usable end-to-end.
* ``attach_session`` and ``prepare_web_attach`` intentionally fail closed; the
  tmux attach subprocess contract has no native Windows equivalent.
* Working directory reports the initial directory only, and foreground command
  discovery is not available through the current backend contract.
* pywinpty exposes a PID after spawning. The backend assigns it to a Windows
  Job Object immediately so teardown owns descendants, but unlike a suspended
  CreateProcess/ConPTY launch this has a small child-spawn race. If assignment
  fails, the launch is rejected and the PTY is closed.

The module is safe to import on other platforms; pywinpty and Win32 APIs are
loaded only when a Windows terminal is created.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from typing import Dict, List, Optional

import pyte
import psutil

from cli_agent_orchestrator.backends.base import (
    TerminalBackend,
    TerminalBackendError,
    TerminalCleanupOutcome,
    TerminalCleanupResult,
    TerminalNotFoundError,
)
logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_HISTORY_LINES = 5000
_SUBSCRIBER_QUEUE_SIZE = 128
_PTY_ROWS = 50
_PTY_COLS = 220
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class _WindowsJob:
    """Kill-on-close Job Object for one CLI and its descendants."""

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self, pid: int) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are only available on Windows")

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        api = self._kernel32
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.QueryInformationJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL

        self._handle = api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._closed = False
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

        process = api.OpenProcess(
            self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE | self._PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(pid),
        )
        if not process:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        try:
            if not api.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        finally:
            api.CloseHandle(process)

    def terminate(self, timeout: float = 5.0) -> bool:
        if self._closed:
            return True
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        # A false return can mean the job is already empty. Query the active
        # count below before deciding whether cleanup is unconfirmed.
        self._kernel32.TerminateJobObject(self._handle, 1)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            accounting = Accounting()
            if not self._kernel32.QueryInformationJobObject(
                self._handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.ActiveProcesses == 0:
                return True
            time.sleep(0.02)
        return False

    def close(self) -> None:
        if self._handle and not self._closed:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            self._closed = True


@dataclass
class _Terminal:
    session_name: str
    window_name: str
    terminal_id: str
    process: object
    cwd: Optional[str]
    command: List[str]
    job: Optional[_WindowsJob]
    screen: object
    stream: object
    output: deque[bytes] = field(default_factory=deque)
    output_size: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    stop_reader: threading.Event = field(default_factory=threading.Event)
    reader: Optional[threading.Thread] = None
    subscribers: set[queue.Queue[bytes]] = field(default_factory=set)
    exited: bool = False
    pipe_path: Optional[str] = None


class WindowsBackend(TerminalBackend):
    """Native terminal backend backed by a Windows pseudo console."""

    def __init__(self, history_bytes: int = _MAX_OUTPUT_BYTES) -> None:
        if os.name != "nt":
            raise TerminalBackendError("WindowsBackend requires native Windows")
        if not isinstance(history_bytes, int) or history_bytes < 64 * 1024:
            raise ValueError("history_bytes must be an integer of at least 64 KiB")
        self._history_bytes = history_bytes
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, _Terminal]] = {}
        self._terminals: Dict[str, _Terminal] = {}

    def supports_output_stream(self) -> bool:
        return True

    def shutdown(self) -> None:
        """Stop every PTY process owned by this server before its handles close."""
        with self._lock:
            sessions = list(self._sessions)
        for session_name in sessions:
            if not self.kill_session(session_name):
                logger.error("Could not confirm native Windows session shutdown: %s", session_name)

    def _lookup(self, session_name: str, window_name: str) -> _Terminal:
        with self._lock:
            session = self._sessions.get(session_name)
            terminal = session.get(window_name) if session else None
        if terminal is None:
            raise TerminalNotFoundError(f"{session_name}:{window_name}")
        return terminal

    @staticmethod
    def _environment(terminal_id: str, extra_env: Optional[Dict[str, str]]) -> Dict[str, str]:
        # Mirror CAO's conservative tmux environment allowlist rather than
        # forwarding arbitrary server secrets into an agent process.
        essential = {
            "ALLUSERSPROFILE", "APPDATA", "COMSPEC", "HOME", "HOMEDRIVE",
            "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT", "PROGRAMDATA",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PUBLIC", "SHELL",
            "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE",
            "USERNAME", "USERDOMAIN", "USER", "WINDIR", "LANG", "TERM",
            "DO_NOT_TRACK",
        }
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in essential
            or key.startswith(("CAO_", "KIRO_", "MISE_", "AWS_"))
        }
        for key, value in (extra_env or {}).items():
            if not isinstance(key, str) or not isinstance(value, str) or "=" in key or "\0" in key + value:
                raise ValueError("Invalid environment variable")
            if key.upper() in {"CAO_TERMINAL_ID"}:
                raise ValueError(f"Environment variable {key} is reserved")
            env[key] = value
        env["CAO_TERMINAL_ID"] = terminal_id
        env.setdefault("HOME", env.get("USERPROFILE", ""))
        # The desktop host may pass TERM=dumb to its child server.  Codex then
        # blocks at an interactive confirmation prompt even though ConPTY is a
        # real terminal.  Give every native pseudo-console a capable TERM.
        if not env.get("TERM") or env["TERM"].lower() == "dumb":
            env["TERM"] = "xterm-256color"
        return env

    @staticmethod
    def _shell_command(window_shell: Optional[str]) -> List[str]:
        shell = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or r"C:\Windows\System32\cmd.exe"
        if window_shell:
            # Provider initialization passes shell commands to the terminal.
            # Start them through cmd.exe so quoted paths and shell syntax retain
            # the same meaning users expect in a native Windows console.
            return [shell, "/d", "/s", "/c", window_shell]
        return [shell, "/d", "/q"]

    def _spawn(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str],
        window_shell: Optional[str],
        extra_env: Optional[Dict[str, str]],
    ) -> _Terminal:
        if not terminal_id or "\0" in terminal_id:
            raise ValueError("terminal_id must be a non-empty string")
        cwd = str(Path(working_directory).resolve()) if working_directory else None
        if cwd and not Path(cwd).is_dir():
            raise ValueError(f"Working directory does not exist: {cwd}")
        argv = self._shell_command(window_shell)
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise TerminalBackendError(
                "Native Windows terminal support requires pywinpty (ConPTY)."
            ) from exc

        try:
            process = PtyProcess.spawn(
                argv,
                cwd=cwd,
                env=self._environment(terminal_id, extra_env),
                dimensions=(_PTY_ROWS, _PTY_COLS),
            )
        except Exception as exc:
            raise TerminalBackendError(f"Could not start Windows pseudo-console: {exc}") from exc

        job = None
        try:
            pid = int(getattr(process, "pid"))
            job = _WindowsJob(pid)
        except Exception as exc:
            try:
                process.close(force=True)
            except Exception:
                logger.exception("Could not close a PTY whose Job Object assignment failed")
            raise TerminalBackendError(
                f"Could not establish Windows process-tree ownership for terminal {terminal_id}: {exc}"
            ) from exc

        terminal = _Terminal(
            session_name=session_name,
            window_name=window_name,
            terminal_id=terminal_id,
            process=process,
            cwd=cwd,
            command=argv,
            job=job,
            screen=pyte.HistoryScreen(_PTY_COLS, _PTY_ROWS, history=_MAX_HISTORY_LINES),
            stream=None,
        )
        terminal.stream = pyte.Stream(terminal.screen)
        terminal.reader = threading.Thread(
            target=self._reader_loop,
            args=(terminal,),
            name=f"cao-conpty-{terminal_id[:8]}",
            daemon=True,
        )
        terminal.reader.start()
        return terminal

    def create_session(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> str:
        with self._lock:
            if session_name in self._sessions:
                raise TerminalBackendError(f"Session already exists: {session_name}")
            if terminal_id in self._terminals:
                raise TerminalBackendError(f"Terminal ID already exists: {terminal_id}")
        terminal = self._spawn(
            session_name, window_name, terminal_id, working_directory, None, extra_env,
        )
        with self._lock:
            if session_name in self._sessions or terminal_id in self._terminals:
                self._dispose(terminal)
                raise TerminalBackendError("Session or terminal ID was concurrently claimed")
            self._sessions[session_name] = {window_name: terminal}
            self._terminals[terminal_id] = terminal
        return window_name

    def session_exists(self, session_name: str) -> bool:
        with self._lock:
            return session_name in self._sessions

    def session_exists_strict(self, session_name: str) -> bool:
        return self.session_exists(session_name)

    def list_sessions(self) -> List[Dict[str, str]]:
        with self._lock:
            snapshot = [(name, list(windows.values())) for name, windows in self._sessions.items()]
        return [
            {
                "id": name,
                "name": name,
                "status": "active" if any(self._is_alive(t) for t in terminals) else "completed",
            }
            for name, terminals in snapshot
        ]

    def kill_session(self, session_name: str) -> bool:
        with self._lock:
            windows = self._sessions.get(session_name)
            if windows is None:
                return False
            terminals = list(windows.values())
        outcomes = [self._dispose(terminal) for terminal in terminals]
        if not all(outcomes):
            return False
        with self._lock:
            current = self._sessions.get(session_name)
            if current is windows:
                self._sessions.pop(session_name, None)
                for terminal in terminals:
                    self._terminals.pop(terminal.terminal_id, None)
        return True

    def create_window(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        window_shell: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> str:
        with self._lock:
            session = self._sessions.get(session_name)
            if session is None:
                raise TerminalNotFoundError(session_name, f"Session not found: {session_name}")
            if window_name in session:
                raise TerminalBackendError(f"Window already exists: {session_name}:{window_name}")
            if terminal_id in self._terminals:
                raise TerminalBackendError(f"Terminal ID already exists: {terminal_id}")
        terminal = self._spawn(
            session_name, window_name, terminal_id, working_directory, window_shell, extra_env,
        )
        with self._lock:
            current = self._sessions.get(session_name)
            if current is not session or window_name in current or terminal_id in self._terminals:
                self._dispose(terminal)
                raise TerminalBackendError("Session or terminal ID was concurrently changed")
            current[window_name] = terminal
            self._terminals[terminal_id] = terminal
        return window_name

    def kill_window(self, session_name: str, window_name: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_name)
            terminal = session.get(window_name) if session else None
        if terminal is None:
            return False
        if not self._dispose(terminal):
            return False
        with self._lock:
            session = self._sessions.get(session_name)
            if session and session.get(window_name) is terminal:
                session.pop(window_name, None)
                self._terminals.pop(terminal.terminal_id, None)
                if not session:
                    self._sessions.pop(session_name, None)
        return True

    def cleanup_terminal_exact(
        self,
        terminal_id: str,
        session_name: Optional[str] = None,
        window_name: Optional[str] = None,
        *,
        close: bool = True,
    ) -> TerminalCleanupResult:
        with self._lock:
            terminal = self._terminals.get(terminal_id)
            matches = [t for t in self._terminals.values() if t.terminal_id == terminal_id]
        if not matches:
            return TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)
        if len(matches) != 1 or terminal is None:
            return TerminalCleanupResult(TerminalCleanupOutcome.UNKNOWN, "Ambiguous terminal identity")
        if session_name and terminal.session_name != session_name:
            return TerminalCleanupResult(TerminalCleanupOutcome.UNKNOWN, "Session hint does not match terminal identity")
        if window_name and terminal.window_name != window_name:
            return TerminalCleanupResult(TerminalCleanupOutcome.UNKNOWN, "Window hint does not match terminal identity")
        if not close:
            return TerminalCleanupResult(TerminalCleanupOutcome.STILL_PRESENT)
        if not self._dispose(terminal):
            return TerminalCleanupResult(TerminalCleanupOutcome.UNKNOWN, "Process-tree shutdown could not be confirmed")
        with self._lock:
            if self._terminals.get(terminal_id) is terminal:
                self._terminals.pop(terminal_id, None)
                session = self._sessions.get(terminal.session_name)
                if session and session.get(terminal.window_name) is terminal:
                    session.pop(terminal.window_name, None)
                    if not session:
                        self._sessions.pop(terminal.session_name, None)
        return TerminalCleanupResult(TerminalCleanupOutcome.DELETED)

    def send_keys(
        self,
        session_name: str,
        window_name: str,
        keys: str,
        enter_count: int = 1,
        force_bracketed_paste: bool = False,
        submit_delay: float = 0.3,
    ) -> None:
        if not isinstance(keys, str) or not isinstance(enter_count, int) or enter_count < 0:
            raise ValueError("Invalid terminal input")
        terminal = self._lookup(session_name, window_name)
        text = f"\x1b[200~{keys}\x1b[201~" if force_bracketed_paste else keys
        self._write(terminal, text)
        if enter_count:
            if submit_delay > 0:
                time.sleep(min(float(submit_delay), 2.0))
            self._write(terminal, "\r" * enter_count)

    def send_special_key(self, session_name: str, window_name: str, key: str) -> None:
        controls = {f"C-{chr(letter)}": chr(letter - 96) for letter in range(ord("a"), ord("z") + 1)}
        special = {
            "Enter": "\r", "C-m": "\r", "Tab": "\t", "C-i": "\t",
            "Escape": "\x1b", "C-[": "\x1b", "Backspace": "\x7f", "C-h": "\x08",
            "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
            "Home": "\x1b[H", "End": "\x1b[F", "PageUp": "\x1b[5~", "PageDown": "\x1b[6~",
            "Delete": "\x1b[3~", "C-c": "\x03", "C-d": "\x04",
        }
        payload = special.get(key, controls.get(key))
        if payload is None:
            raise ValueError(f"Unsupported Windows terminal key: {key}")
        self._write(self._lookup(session_name, window_name), payload)

    @property
    def max_history_lines(self) -> int:
        return _MAX_HISTORY_LINES

    def get_history(
        self,
        session_name: str,
        window_name: str,
        tail_lines: Optional[int] = None,
        strip_escapes: bool = False,
        full_history: bool = False,
        visible_only: bool = False,
        join_wrapped: bool = False,
    ) -> str:
        terminal = self._lookup(session_name, window_name)
        with terminal.lock:
            top = [
                "".join(row[column].data for column in range(terminal.screen.columns)).rstrip()
                for row in terminal.screen.history.top
            ]
            visible = [row.rstrip() for row in terminal.screen.display]
            while visible and not visible[-1]:
                visible.pop()
            text = "\n".join(visible if visible_only else top + visible)
        lines = text.splitlines()
        if not full_history and not visible_only:
            limit = min(tail_lines or 1000, _MAX_HISTORY_LINES)
            lines = lines[-max(1, limit):]
        result = "\n".join(lines)
        if strip_escapes:
            result = _ANSI_ESCAPE.sub("", result)
        return result

    def get_pane_working_directory(self, session_name: str, window_name: str) -> Optional[str]:
        return self._lookup(session_name, window_name).cwd

    def get_pane_current_command(self, session_name: str, window_name: str) -> Optional[str]:
        terminal = self._lookup(session_name, window_name)
        shell_name = Path(terminal.command[0]).name.lower()
        try:
            process = psutil.Process(int(terminal.process.pid))
            if not process.is_running():
                return shell_name
            descendants = []
            unreadable_child = False
            for child in process.children(recursive=True):
                try:
                    if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                        # Tools may exit between any two psutil calls. Snapshot
                        # each child independently so one disappearing tool
                        # cannot make the still-running Codex look like cmd.exe.
                        descendants.append((child.name().lower(), child.create_time()))
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, OSError):
                    unreadable_child = True
            if not descendants:
                return None if unreadable_child else shell_name
            agent_descendants = [
                child for child in descendants
                if child[0] not in {"cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe"}
            ]
            if agent_descendants:
                return max(agent_descendants, key=lambda child: child[1])[0]
            if unreadable_child:
                return None
            return max(descendants, key=lambda child: child[1])[0]
        except psutil.NoSuchProcess:
            return shell_name
        except (psutil.AccessDenied, OSError):
            # An unreadable process tree cannot prove the provider has exited.
            return None

    def attach_session(self, session_name: str) -> None:
        raise TerminalBackendError(
            "Native Windows interactive attach must use the application's WebSocket PTY bridge."
        )

    def prepare_web_attach(self, session_name: str, window_name: str) -> List[str]:
        terminal = self._lookup(session_name, window_name)
        raise TerminalBackendError(
            f"Terminal {terminal.terminal_id} must be attached through subscribe_output/send_raw."
        )

    def pipe_pane(self, session_name: str, window_name: str, file_path: str) -> None:
        # Output is continuously captured by _reader_loop. POSIX FIFO consumers
        # must instead subscribe to that stream on Windows.
        terminal = self._lookup(session_name, window_name)
        terminal.pipe_path = file_path

    def stop_pipe_pane(self, session_name: str, window_name: str) -> None:
        terminal = self._lookup(session_name, window_name)
        terminal.pipe_path = None

    def subscribe_output(self, terminal_id: str) -> queue.Queue[bytes]:
        """Subscribe to future output chunks; queues are bounded and drop oldest."""
        with self._lock:
            terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise TerminalNotFoundError(terminal_id)
        subscriber: queue.Queue[bytes] = queue.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        with terminal.lock:
            exited = terminal.exited
            if not exited:
                terminal.subscribers.add(subscriber)
            # A session can emit its initial prompt before terminal_service has
            # installed the output-stream pump. Replay the retained tail so the
            # first status snapshot is not based on an empty buffer.
            for chunk in terminal.output:
                self._publish(subscriber, chunk)
            if exited:
                self._publish_eof(subscriber)
        return subscriber

    def unsubscribe_output(self, terminal_id: str, subscriber: queue.Queue[bytes]) -> None:
        with self._lock:
            terminal = self._terminals.get(terminal_id)
        if terminal is not None:
            with terminal.lock:
                terminal.subscribers.discard(subscriber)

    def send_raw(self, terminal_id: str, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        with self._lock:
            terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise TerminalNotFoundError(terminal_id)
        self._write(terminal, data.decode("utf-8", errors="replace"))

    def resize_terminal(self, terminal_id: str, rows: int, cols: int) -> None:
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (rows, cols)):
            raise ValueError("rows and cols must be integers")
        if not 2 <= rows <= 500 or not 2 <= cols <= 1000:
            raise ValueError("Terminal dimensions are out of range")
        with self._lock:
            terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise TerminalNotFoundError(terminal_id)
        try:
            terminal.process.setwinsize(rows, cols)
            with terminal.lock:
                terminal.screen.resize(lines=rows, columns=cols)
        except Exception as exc:
            raise TerminalBackendError(f"Could not resize terminal {terminal_id}: {exc}") from exc

    @staticmethod
    def _is_alive(terminal: _Terminal) -> bool:
        try:
            return bool(terminal.process.isalive())
        except Exception:
            return False

    @staticmethod
    def _write(terminal: _Terminal, text: str) -> None:
        if not WindowsBackend._is_alive(terminal):
            raise TerminalBackendError(f"Terminal process has exited: {terminal.terminal_id}")
        try:
            terminal.process.write(text)
        except Exception as exc:
            raise TerminalBackendError(f"Could not write to terminal {terminal.terminal_id}: {exc}") from exc

    def _reader_loop(self, terminal: _Terminal) -> None:
        try:
            while not terminal.stop_reader.is_set():
                try:
                    chunk = terminal.process.read(4096)
                except EOFError:
                    break
                except OSError:
                    if terminal.stop_reader.is_set() or not self._is_alive(terminal):
                        break
                    raise
                if not chunk:
                    if not self._is_alive(terminal):
                        break
                    time.sleep(0.01)
                    continue
                payload = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else bytes(chunk)
                with terminal.lock:
                    try:
                        terminal.stream.feed(chunk if isinstance(chunk, str) else payload.decode("utf-8", errors="replace"))
                    except Exception:
                        logger.debug("Could not compose ConPTY screen for %s", terminal.terminal_id, exc_info=True)
                    terminal.output.append(payload)
                    terminal.output_size += len(payload)
                    while terminal.output_size > self._history_bytes and terminal.output:
                        terminal.output_size -= len(terminal.output.popleft())
                    subscribers = tuple(terminal.subscribers)
                for subscriber in subscribers:
                    self._publish(subscriber, payload)
        except Exception:
            logger.exception("ConPTY output reader failed for terminal %s", terminal.terminal_id)
        finally:
            with terminal.lock:
                terminal.exited = True
                subscribers = tuple(terminal.subscribers)
                terminal.subscribers.clear()
            for subscriber in subscribers:
                self._publish_eof(subscriber)

    @staticmethod
    def _publish(subscriber: queue.Queue[bytes], payload: bytes) -> None:
        try:
            subscriber.put_nowait(payload)
            return
        except queue.Full:
            pass
        try:
            subscriber.get_nowait()
        except queue.Empty:
            pass
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            pass

    @staticmethod
    def _publish_eof(subscriber: queue.Queue[bytes]) -> None:
        while True:
            try:
                subscriber.put_nowait(b"")
                return
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    return

    def _dispose(self, terminal: _Terminal) -> bool:
        if terminal.job is not None:
            try:
                stopped = terminal.job.terminate()
            except Exception:
                logger.exception("Could not terminate Job Object for %s", terminal.terminal_id)
                stopped = False
            finally:
                terminal.job.close()
        else:
            stopped = False
        terminal.stop_reader.set()
        if stopped:
            try:
                terminal.process.close(force=False)
            except Exception:
                logger.debug("PTY close after Job Object termination failed", exc_info=True)
        else:
            # Do not claim teardown when Windows could not confirm the owned
            # process tree has exited. pywinpty's close is only a direct-child
            # fallback and is not treated as a confirmed process-tree cleanup.
            try:
                terminal.process.close(force=False)
            except Exception:
                logger.debug("PTY close during incomplete teardown failed", exc_info=True)
        if terminal.reader and terminal.reader is not threading.current_thread():
            terminal.reader.join(timeout=2.0)
        provider_reader = getattr(terminal.process, "_thread", None)
        if provider_reader and provider_reader is not threading.current_thread():
            provider_reader.join(timeout=2.0)
        return stopped and not self._is_alive(terminal) and not (terminal.reader and terminal.reader.is_alive())
