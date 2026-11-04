"""Portable process-identity regressions against the shipped lease implementation."""
import os
import psutil
from manager.resource_locks import _owner_alive


def test_live_process_identity_is_preserved():
    process = psutil.Process(os.getpid())
    assert _owner_alive(f"{process.pid}:{process.create_time()}:test-owner") is True


def test_pid_reuse_identity_does_not_keep_stale_owner():
    process = psutil.Process(os.getpid())
    assert _owner_alive(f"{process.pid}:{process.create_time() - 100}:stale-owner") is False


def test_legacy_unknown_owner_is_not_permission_to_steal():
    assert _owner_alive("legacy-owner-without-process-identity") is True
