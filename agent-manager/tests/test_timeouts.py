"""Verify the global pytest timeout discipline."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

GLOBAL_TIMEOUT = 30


def test_timeout_plugin_is_loaded(request):
    assert request.config.pluginmanager.hasplugin("timeout")


def test_global_timeout_is_configured(request):
    assert str(request.config.getini("timeout")) == str(GLOBAL_TIMEOUT)


def test_two_second_sleep_stays_within_global_limit(request):
    assert str(request.config.getini("timeout")) == str(GLOBAL_TIMEOUT)
    started = time.monotonic()
    time.sleep(2)
    assert time.monotonic() - started < GLOBAL_TIMEOUT


def test_per_test_timeout_fails_a_slow_test(tmp_path):
    inner = tmp_path / "test_inner_slow.py"
    inner.write_text(
        textwrap.dedent(
            """
            import time

            import pytest


            @pytest.mark.timeout(1)
            def test_slower_than_its_own_limit():
                time.sleep(3)
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "timeout=30",
            str(inner),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=GLOBAL_TIMEOUT * 2,
    )

    output = (result.stdout + result.stderr).lower()
    assert result.returncode != 0
    assert "timeout" in output
