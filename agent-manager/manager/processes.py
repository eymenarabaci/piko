"""Bounded asynchronous subprocesses, owned process trees and secret-free lifecycle records."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from uuid import uuid4

import psutil

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_ledger_lock = threading.Lock()


class ProcessError(RuntimeError):
    pass


async def _join_owned(task):
    """Join an operation even if its caller is cancelled repeatedly.

    A to_thread write cannot actually be stopped by cancelling its asyncio wrapper.
    Returning only after it finishes keeps temporary directories and process handles
    owned until their side effects have completed.
    """
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
            if task.done():
                return task.result(), cancelled


async def _complete_owned(awaitable):
    value, cancelled = await _join_owned(asyncio.create_task(awaitable))
    if cancelled:
        raise asyncio.CancelledError
    return value


def _record(root: Path, entry: dict) -> None:
    with _ledger_lock:
        path = root / 'work' / 'resource-ledger.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {'owner_task': 'agent-manager', 'resources': []}
        rows = data.setdefault('resources', [])
        previous = next((r for r in rows if r.get('resource_id') == entry['resource_id']), None)
        if previous is None:
            rows.append(entry)
        else:
            previous.update(entry)
        data['resources'] = rows[-500:]
        try:
            import uuid
            temporary = path.with_name(f"resource-ledger-{uuid.uuid4().hex[:8]}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
        except Exception:
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        a = self.api
        a.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        a.CreateJobObjectW.restype = w.HANDLE
        a.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        a.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        a.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        a.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        a.OpenProcess.restype = w.HANDLE
        a.CloseHandle.argtypes = [w.HANDLE]
        class Basic(ctypes.Structure):
            _fields_ = [('user', ctypes.c_int64), ('job', ctypes.c_int64), ('flags', w.DWORD),
                        ('min', ctypes.c_size_t), ('max', ctypes.c_size_t), ('active', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('schedule', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(f'x{i}', ctypes.c_uint64) for i in range(6)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('memory', ctypes.c_size_t),
                        ('jobmemory', ctypes.c_size_t), ('peak', ctypes.c_size_t), ('jobpeak', ctypes.c_size_t)]
        self.handle = a.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not a.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ProcessError('Windows işlem grubu oluşturulamadı.')

    def attach_and_resume(self, pid):
        c, a = self.ctypes, self.api
        from ctypes import wintypes as w
        handle = a.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise ProcessError('CLI işlem grubu açılamadı.')
        try:
            if not a.AssignProcessToJobObject(self.handle, handle):
                raise ProcessError('CLI sahipli işlem grubuna atanamadı.')
        finally:
            a.CloseHandle(handle)
        # Process starts suspended; it cannot create an unowned descendant.
        class ThreadEntry(c.Structure):
            _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD),
                        ('pid', w.DWORD), ('base', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]
        a.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        a.CreateToolhelp32Snapshot.restype = w.HANDLE
        a.Thread32First.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
        a.Thread32Next.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
        a.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        a.OpenThread.restype = w.HANDLE
        a.ResumeThread.argtypes = [w.HANDLE]
        a.ResumeThread.restype = w.DWORD
        snapshot = a.CreateToolhelp32Snapshot(4, 0)
        if snapshot == c.c_void_p(-1).value:
            raise ProcessError('CLI başlangıç iş parçacığı bulunamadı.')
        try:
            row = ThreadEntry()
            row.size = c.sizeof(row)
            found = a.Thread32First(snapshot, c.byref(row))
            while found:
                if row.pid == pid:
                    thread = a.OpenThread(2, False, row.tid)
                    if not thread:
                        raise ProcessError('CLI iş parçacığı açılamadı.')
                    try:
                        if a.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ProcessError('CLI başlatılamadı.')
                    finally:
                        a.CloseHandle(thread)
                    return
                found = a.Thread32Next(snapshot, c.byref(row))
            raise ProcessError('CLI başlangıç iş parçacığı bulunamadı.')
        finally:
            a.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


async def run_command(command: list[str], *, root: Path, workspace: Path,
                      timeout: float, on_event, stdin: str | None = None,
                      env: dict[str, str] | None = None) -> dict:
    """Return {returncode, stdout, stderr}; no shell, no automatic retry.

    Output has a shared 2 MiB cap. Heartbeats report activity, never infer a failure.
    """
    if not command or timeout <= 0:
        raise ProcessError('Geçersiz işlem isteği.')
    job = _WindowsJob() if os.name == 'nt' else None
    proc = None
    tasks = []
    entry = None
    state = 'failed'
    started = time.monotonic()
    last_activity = started
    total = 0
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW | 0x4} if job else {'start_new_session': True}
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=str(workspace),
                    stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, **options))
        # Retain the returned process handle even when cancellation races OS spawn.
        proc, spawn_cancelled = await _join_owned(spawning)
        identity = psutil.Process(proc.pid)
        entry = {'resource_id': uuid4().hex, 'owner_task': 'agent-manager-cli', 'pid': proc.pid,
                 'created_utc': datetime.fromtimestamp(identity.create_time(), timezone.utc).isoformat(),
                 'executable_path': identity.exe(), 'parent_pid': os.getpid(),
                 'shared': False, 'disposable': True, 'state': 'running'}
        if job and not spawn_cancelled:
            job.attach_and_resume(proc.pid)
        await _complete_owned(asyncio.to_thread(_record, root, dict(entry)))
        if spawn_cancelled:
            raise asyncio.CancelledError

        async def drain(name, stream):
            nonlocal total, last_activity
            while chunk := await stream.read(8192):
                total += len(chunk)
                last_activity = time.monotonic()
                if total > MAX_OUTPUT_BYTES:
                    raise ProcessError('CLI çıktısı 2 MiB sınırını aştı.')
                buffers[name].extend(chunk)

        async def feed():
            if proc.stdin:
                try:
                    proc.stdin.write(stdin.encode('utf-8'))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

        async def heartbeat():
            previous = -1
            while True:
                await asyncio.sleep(5)
                elapsed = round(time.monotonic() - started, 1)
                await on_event('provider_activity', 'CLI çalışıyor; yanıt bekleniyor.',
                               {'elapsed_seconds': elapsed, 'idle_seconds': round(time.monotonic() - last_activity, 1),
                                'received_bytes': total, 'output_changed': total != previous})
                previous = total

        await on_event('provider_started', 'CLI işlemi başlatıldı.', {'pid': proc.pid})
        tasks = [asyncio.create_task(drain('stdout', proc.stdout)),
                 asyncio.create_task(drain('stderr', proc.stderr)), asyncio.create_task(feed())]
        pulse = asyncio.create_task(heartbeat())
        tasks.append(pulse)
        try:
            async with asyncio.timeout(max(.01, timeout - (time.monotonic() - started))):
                await asyncio.gather(*tasks[:-1], proc.wait())
        finally:
            pulse.cancel()
        state = 'completed'
        return {'returncode': proc.returncode,
                **{k: bytes(v).decode('utf-8', 'replace') for k, v in buffers.items()}}
    except TimeoutError as exc:
        state = 'timeout'
        raise ProcessError('CLI toplam süre sınırını aştı.') from exc
    except asyncio.CancelledError:
        state = 'cancelled'
        raise
    finally:
        async def cleanup():
            nonlocal state
            if job:
                job.close()
            elif proc:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if proc:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    state = 'cleanup_failed'
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if entry:
                entry['state'] = state
                await _complete_owned(asyncio.to_thread(_record, root, dict(entry)))
            if state == 'cleanup_failed':
                raise ProcessError('CLI süreç temizliği süre sınırında doğrulanamadı.')
        await _complete_owned(cleanup())
