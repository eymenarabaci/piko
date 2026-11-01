"""Tests for the CLI runtime registry, capability negotiation and quota choice.

Everything is offline: PATH points at a temporary directory and the version
runner is injected, so no real CLI is executed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from manager import cli_runtime as cr

UTC = timezone.utc


def _fake_exe(directory: Path, name: str) -> Path:
    filename = name + (".exe" if os.name == "nt" else "")
    path = directory / filename
    path.write_text("", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


def _key(model: str = "deepseek", runtime: str = "opencode",
         account: str = "work") -> cr.ModelKey:
    return cr.ModelKey(model, runtime, account)


def _bucket(remaining: float, minutes: float | None, *,
            model: str = "deepseek", runtime: str = "opencode",
            account: str = "work") -> cr.BucketSnapshot:
    reset = None if minutes is None else datetime.now(UTC) + timedelta(minutes=minutes)
    return cr.BucketSnapshot(
        _key(model=model, runtime=runtime, account=account),
        remaining,
        reset_at=reset,
    )


# --- probe ------------------------------------------------------------------


def test_probe_finds_runtime_on_fake_path(tmp_path):
    exe = _fake_exe(tmp_path, "opencode")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return "opencode 1.4.2\n"

    result = cr.probe(
        cr.get_manifest("opencode"),
        environ={"PATH": str(tmp_path)},
        runner=runner,
    )

    assert result.available is True
    assert result.executable == str(exe)
    assert result.version == "1.4.2"
    assert calls == [[str(exe), "--version"]]


def test_probe_reports_missing_without_raising(tmp_path):
    result = cr.probe(
        cr.get_manifest("claude-code"),
        environ={"PATH": str(tmp_path)},
        runner=lambda argv, **kwargs: "claude 9.9.9",
    )

    assert result.available is False
    assert result.executable is None
    assert result.version is None
    assert "bulunamadi" in result.detail


def test_probe_version_failure_still_available(tmp_path):
    _fake_exe(tmp_path, "opencode")

    def runner(argv, **kwargs):
        raise OSError("cannot execute")

    result = cr.probe(
        cr.get_manifest("opencode"),
        environ={"PATH": str(tmp_path)},
        runner=runner,
    )

    assert result.available is True
    assert result.version is None
    assert "OSError" in result.detail


def test_probe_all_covers_every_registered_runtime():
    results = cr.probe_all(which=lambda name: None)

    assert tuple(result.runtime_id for result in results) == cr.REGISTRY.ids()
    assert cr.REGISTRY.ids() == ("opencode", "claude-code", "cao")
    assert all(result.available is False for result in results)


def test_parse_version_extracts_semver_from_noisy_output():
    assert cr.parse_version("Claude Code v2.1.0 (build 7)") == "2.1.0"
    assert cr.parse_version("no version here") is None


# --- capability negotiation -------------------------------------------------


def test_negotiate_accepts_declared_capabilities():
    decision = cr.negotiate(
        cr.get_manifest("claude-code"),
        require=("resume", "mcp", "noninteractive"),
    )

    assert decision.ok is True
    assert decision.missing == ()


def test_negotiate_reports_missing_capabilities():
    manifest = cr.RuntimeManifest(
        id="minimal",
        title="Minimal",
        executables=("minimal.exe",),
        capabilities=cr.Capabilities(resume=True),
    )

    decision = cr.negotiate(manifest, require=("resume", "mcp", "noninteractive"))

    assert decision.ok is False
    assert decision.missing == ("mcp", "noninteractive")
    assert len(decision.reasons) == 2


def test_negotiate_rejects_unknown_capability():
    with pytest.raises(ValueError):
        cr.negotiate(cr.get_manifest("opencode"), require=("telepathy",))


def test_noninteractive_capability_requires_an_approval_flag():
    with pytest.raises(ValueError):
        cr.Capabilities(noninteractive=True)


# --- model identity triple --------------------------------------------------


def test_model_key_is_the_model_runtime_account_triple():
    base = cr.ModelKey("deepseek", "opencode", "work")

    assert base == cr.ModelKey(" deepseek ", "opencode", "work")
    assert hash(base) == hash(cr.ModelKey("deepseek", "opencode", "work"))
    assert base != cr.ModelKey("deepseek", "claude-code", "work")
    assert base != cr.ModelKey("deepseek", "opencode", "personal")
    assert base != cr.ModelKey("deepseek-v2", "opencode", "work")


@pytest.mark.parametrize("field", ["model", "runtime", "account"])
def test_model_key_rejects_blank_components(field):
    values = {"model": "deepseek", "runtime": "opencode", "account": "work"}
    values[field] = "  "
    with pytest.raises(ValueError):
        cr.ModelKey(**values)


# --- quota bucket selection -------------------------------------------------


def test_choose_bucket_skips_empty_buckets():
    empty = _bucket(0.0, minutes=1)
    live = _bucket(0.4, minutes=30)

    assert cr.choose_bucket([empty, live]) is live
    assert cr.choose_bucket([empty]) is None


def test_choose_bucket_orders_by_earliest_reset():
    later = _bucket(0.9, minutes=60, account="later")
    sooner = _bucket(0.2, minutes=5, account="sooner")

    assert cr.choose_bucket([later, sooner]) is sooner


def test_choose_bucket_puts_unknown_reset_last():
    known = _bucket(0.1, minutes=120, account="known")
    unknown = _bucket(0.95, minutes=None, account="unknown")

    assert cr.choose_bucket([unknown, known]) is known


def test_rank_buckets_filters_by_model_and_runtime():
    matching = _bucket(0.5, minutes=10, model="deepseek", runtime="opencode")
    other_model = _bucket(0.9, minutes=1, model="gpt", runtime="opencode")
    other_runtime = _bucket(0.9, minutes=1, model="deepseek", runtime="cao")

    ranked = cr.rank_buckets(
        [other_model, other_runtime, matching],
        model="deepseek",
        runtime="opencode",
    )

    assert ranked == [matching]


def test_next_reset_returns_earliest_future_reset():
    sooner = _bucket(0.0, minutes=3)
    later = _bucket(0.7, minutes=40)
    past = cr.BucketSnapshot(
        _key(account="past"),
        0.0,
        reset_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    assert cr.next_reset([later, sooner, past]) == sooner.reset_at


def test_bucket_snapshot_rejects_invalid_observations():
    with pytest.raises(ValueError):
        cr.BucketSnapshot(_key(), 1.5)
    with pytest.raises(ValueError):
        cr.BucketSnapshot(_key(), 0.5, reset_at=datetime(2026, 1, 1, 12, 0))


# --- registry and adapters --------------------------------------------------


def test_unknown_runtime_raises_unknown_runtime_error():
    with pytest.raises(cr.UnknownRuntimeError):
        cr.get_manifest("does-not-exist")


def test_every_manifest_has_a_planner_that_refuses_to_spawn_yet():
    for manifest in cr.REGISTRY.manifests():
        planner = cr.adapter_signature(manifest)
        assert planner is cr.ADAPTER_SIGNATURES[manifest.id]
        request = cr.InvocationRequest(prompt="hi", workspace=".")
        with pytest.raises(NotImplementedError):
            planner(_key(runtime=manifest.id), request)
