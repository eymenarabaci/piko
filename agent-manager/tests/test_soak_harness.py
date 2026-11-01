"""Kısaltılmış soak koşusu ve arıza enjeksiyonu doğrulaması.

Gerçek 24 saat beklenmez: sahte saat iki "saati" saniyeler içinde ilerletir.
Koşu tamamen geçici dizinde çalışır; canlı model, posta veya takvim yoktur.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import soak_run


@pytest.mark.asyncio
async def test_two_hour_soak_exercises_every_fault(tmp_path):
    summary = await soak_run.run_soak(
        hours=2, jobs=8, seed=11, workdir=tmp_path / "soak")

    assert summary["hours"] == 2
    assert summary["final"]["simulated_hour"] == 2
    assert summary["real_elapsed_seconds"] < 25

    faults = summary["faults"]
    assert faults["crashes"] >= 1
    assert faults["process_runs"] >= 2
    assert faults["restart_returncodes"], "çök-kalk alt süreci hiç çalışmadı"
    assert faults["quota_exhausted"] >= 1
    assert faults["quota_recovered"] >= 1
    assert faults["lock_conflicts"] >= 1
    assert faults["lock_recovered"] >= 1
    assert faults["recovered_operations"] >= 1
    assert summary["recovered_runs"] >= 3

    assert summary["approvals"]["total"] >= 1
    assert summary["red_lines"]["unapproved_apply_refused"] is True
    assert summary["red_lines"]["proactive_draft_refused"] is True
    assert summary["red_lines"]["dangerous_level_refused"] is True

    report = json.loads((tmp_path / "soak" / "saat.json").read_text(encoding="utf-8"))
    assert report["schema"] == "piko-soak/v1"
    assert report["jobs"]["total"] == 8
    assert (report["jobs"]["done"] + report["jobs"]["pending"]
            + report["jobs"]["blocked"]) == 8
    assert (tmp_path / "soak" / "saat-01.json").is_file()
    assert (tmp_path / "soak" / "saat-02.json").is_file()
    markdown = (tmp_path / "soak" / "SOAK_RAPOR.md").read_text(encoding="utf-8")
    assert "PikoSoak Raporu" in markdown
    assert "Çök-kalk" in markdown or "çök" in markdown.lower()


@pytest.mark.asyncio
async def test_soak_decisions_are_reproducible_with_the_same_seed(tmp_path):
    first = await soak_run.run_soak(
        hours=2, jobs=8, seed=3, workdir=tmp_path / "a")
    second = await soak_run.run_soak(
        hours=2, jobs=8, seed=3, workdir=tmp_path / "b")

    assert first["final"]["jobs"] == second["final"]["jobs"]
    assert first["final"]["approvals"] == second["final"]["approvals"]
    assert first["final"]["recovered_runs"] == second["final"]["recovered_runs"]
