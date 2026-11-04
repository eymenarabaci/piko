"""Contract and behaviour tests for the "medya" bridge pack.

The suite runs without ffmpeg/ffprobe/Pillow: WAV fixtures come from the stdlib
``wave`` module and the video fixture is a minimal MP4 header written to a tmp
directory. A single bonus test exercises the real ffmpeg path when it exists.
"""
from __future__ import annotations

import hashlib
import json
import struct
import wave
from pathlib import Path

import pytest

from bridge_packs.medya import adapter as medya
from manager.bridge_gate import BridgeGate
from manager.bridges import BridgeRegistry, validate_bridge_spec, validate_persona_manifest

PACK_DIR = Path(__file__).resolve().parents[1] / "bridge_packs" / "medya"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def manifest() -> dict:
    return json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))


def registry() -> BridgeRegistry:
    bridge_registry = BridgeRegistry()
    bridge_registry.register(manifest())
    return bridge_registry


def write_wav(path: Path, *, seconds: float = 0.5, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def write_fake_mp4(path: Path, *, payload: int = 32) -> Path:
    header = struct.pack(">I", 24) + b"ftypisom" + struct.pack(">I", 0)
    path.write_bytes(header + b"\x00" * payload)
    return path


# ------------------------------------------------------------------- contract


def test_manifest_is_a_complete_contract_without_dangerous():
    spec = validate_bridge_spec(manifest())
    assert spec["id"] == "medya"
    assert spec["levels"] == ["read", "draft", "apply"]
    assert "dangerous" not in spec["levels"]
    assert "dangerous" not in spec["approval"]
    assert spec["idempotency"] == {"type": "keyed", "key": "idempotency_key"}
    assert spec["evidence"]["format"] == "medya-evidence"
    assert set(spec["evidence"]["fields"]) == {
        "source_sha256", "files", "preview_frame", "summary", "capability_used"}
    assert spec["approval"]["apply"] == "per_call_token"


def test_manifest_budget_profile_and_error_taxonomy_are_separate():
    raw = manifest()
    budget = raw["budget"]
    assert budget["max_preview_seconds"] == medya.MAX_PREVIEW_SECONDS == 15.0
    assert budget["max_preview_bytes"] == medya.MAX_PREVIEW_BYTES == 2_000_000
    assert budget["render_seconds_per_minute"] > 0
    assert raw["errors"]["retryable"] and raw["errors"]["needs-user"]
    assert raw["errors"]["external-down"]
    assert not set(raw["errors"]["retryable"]) & set(raw["errors"]["external-down"])
    assert "missing-source" in raw["errors"]["needs-user"]
    assert "ffmpeg-missing" in raw["errors"]["external-down"]


def test_error_payloads_map_to_distinct_classes():
    assert medya.error_payload(
        medya.RetryableMediaError("x"))["error_class"] == "retryable"
    assert medya.error_payload(
        medya.ExternalMediaError("x"))["error_class"] == "external-down"
    assert medya.error_payload(medya.MediaError("x"))["error_class"] == "needs-user"


def test_manager_persona_is_read_and_draft_only():
    validated = validate_persona_manifest(medya.MANAGER_PERSONA, "scheduler")
    assert validated["bridges"]["medya"] == ["read", "draft"]

    gate = BridgeGate(registry())
    gate.check(medya.MANAGER_PERSONA, "medya", "read")
    gate.check(medya.MANAGER_PERSONA, "medya", "draft")
    with pytest.raises(PermissionError):
        gate.check(medya.MANAGER_PERSONA, "medya", "apply",
                   idempotency_key="k1", approval_token="t1")


def test_apply_requires_a_single_use_approval_token():
    gate = BridgeGate(registry())
    with pytest.raises(PermissionError):
        gate.check(medya.APPROVER_PERSONA, "medya", "apply", idempotency_key="k1")
    token = gate.issue_approval("medya", "apply")
    gate.check(medya.APPROVER_PERSONA, "medya", "apply",
               idempotency_key="k1", approval_token=token)
    with pytest.raises(PermissionError):
        gate.check(medya.APPROVER_PERSONA, "medya", "apply",
                   idempotency_key="k2", approval_token=token)


# ------------------------------------------------------------- capability probe


def test_capability_probe_names_a_fallback_for_every_tool(tmp_path):
    caps = medya.discover_capabilities()
    for name in ("ffmpeg", "ffprobe", "pillow"):
        assert isinstance(caps[name]["available"], bool)
        assert caps[name]["fallback"]
    assert caps["behaviors"]["read"]
    assert caps["behaviors"]["thumbnail"]

    written = medya.write_capability_manifest(tmp_path / "capabilities.json")
    on_disk = json.loads((tmp_path / "capabilities.json").read_text(encoding="utf-8"))
    assert on_disk == written
    assert on_disk["bridge"] == "medya"


# ------------------------------------------------------------------ read level


def test_read_wav_uses_stdlib_wave_without_ffprobe(tmp_path):
    write_wav(tmp_path / "ses.wav", seconds=0.5)
    result = medya.read_media(tmp_path, "ses.wav",
                              capabilities=medya.disabled_capabilities())
    assert result["kind"] == "audio"
    assert result["container"] == "wav"
    assert result["duration_seconds"] == pytest.approx(0.5, abs=0.01)
    assert result["sample_rate"] == 8000
    assert result["channels"] == 1
    assert result["capability_used"] == "wave"
    assert result["sha256"] == hashlib.sha256(
        (tmp_path / "ses.wav").read_bytes()).hexdigest()
    assert len(result["summary"].splitlines()) == 3


def test_read_fake_mp4_falls_back_to_magic_bytes(tmp_path):
    write_fake_mp4(tmp_path / "klip.mp4")
    result = medya.read_media(tmp_path, "klip.mp4",
                              capabilities=medya.disabled_capabilities())
    assert result["kind"] == "video"
    assert result["container"] == "mp4"
    assert result["duration_seconds"] is None
    assert result["capability_used"] == "magic-bytes"
    assert result["bytes"] == (tmp_path / "klip.mp4").stat().st_size


def test_inventory_lists_both_assets_and_missing_source_is_needs_user(tmp_path):
    write_wav(tmp_path / "ses.wav")
    write_fake_mp4(tmp_path / "klip.mp4")
    (tmp_path / "notlar.txt").write_text("medya değil", encoding="utf-8")

    result = medya.inventory(tmp_path, "",
                             capabilities=medya.disabled_capabilities())
    assert result["file_count"] == 2
    assert {item["kind"] for item in result["files"]} == {"audio", "video"}
    assert result["summary"].splitlines()[0] == "2 medya dosyası"

    with pytest.raises(medya.MediaError) as error:
        medya.read_media(tmp_path, "yok.mp4",
                         capabilities=medya.disabled_capabilities())
    assert medya.error_payload(error.value)["error_class"] == "needs-user"


# ----------------------------------------------------------------- draft level


def test_draft_plan_is_built_without_writing_anything(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_wav(workspace / "ses.wav", seconds=10.0)
    before = sorted(path.name for path in workspace.iterdir())

    inventory_value = medya.read_media(
        workspace, "ses.wav", capabilities=medya.disabled_capabilities())
    draft = medya.build_draft(
        inventory_value,
        options={"scene_count": 2, "subtitles": ["Açılış", "Kapanış"]})

    assert draft["duration_seconds"] == pytest.approx(10.0, abs=0.01)
    assert len(draft["scenes"]) == 2
    assert draft["trim_points"][0] == {"start": 0.0, "end": 5.0}
    assert [item["text"] for item in draft["subtitles"]] == ["Açılış", "Kapanış"]
    assert draft["thumbnail_times"] and all(
        0 <= value <= 10 for value in draft["thumbnail_times"])
    assert draft["planned_outputs"] == ["thumbnail", "preview", "strip"]
    assert len(draft["summary"].splitlines()) == 3
    assert sorted(path.name for path in workspace.iterdir()) == before, \
        "taslak diske yazmamalı"


# ------------------------------------------------------------------ apply level


def test_apply_falls_back_with_only_stdlib_png(tmp_path):
    write_fake_mp4(tmp_path / "klip.mp4")
    source_sha = hashlib.sha256(
        (tmp_path / "klip.mp4").read_bytes()).hexdigest()

    result = medya.apply_media(
        tmp_path, "klip.mp4", options={"outputs": ["thumbnail", "preview",
                                                   "strip"]},
        capabilities=medya.disabled_capabilities())

    assert result["ok"] is True
    by_kind = {item["kind"]: item for item in result["outputs"]}
    assert by_kind["thumbnail"]["status"] == "fallback"
    assert by_kind["thumbnail"]["capability"] == "stdlib-png"
    assert by_kind["preview"]["status"] == "fallback"
    assert by_kind["preview"]["path"] is None
    thumbnail = tmp_path / by_kind["thumbnail"]["path"]
    assert thumbnail.read_bytes().startswith(PNG_SIGNATURE)
    assert by_kind["strip"]["status"] == "fallback"
    assert source_sha == result["source_sha256"]
    for item in result["files"]:
        written = tmp_path / item["path"]
        assert written.is_file()
        assert hashlib.sha256(written.read_bytes()).hexdigest() == item["sha256"]
    frame = tmp_path / result["preview_frame"]
    assert frame.is_file() and frame.read_bytes().startswith(PNG_SIGNATURE)
    assert result["preview_seconds"] <= 15.0
    assert result["preview_max_bytes"] <= 2_000_000
    assert result["timeout_seconds"] > 0

    inventory_value = medya.read_media(
        tmp_path, "klip.mp4", capabilities=medya.disabled_capabilities())
    evidence = medya.build_evidence(inventory_value, result)
    assert evidence["format"] == "medya-evidence"
    assert evidence["ok"] is True
    assert evidence["source_sha256"] == source_sha
    assert evidence["preview_frame"] == result["preview_frame"]
    assert evidence["sha256"][by_kind["thumbnail"]["path"]] == \
        by_kind["thumbnail"]["sha256"]
    assert "stdlib-png" in evidence["summary"]
    assert len(evidence["summary"].splitlines()) == 3


def test_apply_is_idempotent_per_output_file(tmp_path):
    write_fake_mp4(tmp_path / "klip.mp4")
    first = medya.apply_media(
        tmp_path, "klip.mp4", options={"outputs": ["thumbnail"]},
        capabilities=medya.disabled_capabilities())
    hashes = {item["path"]: item["sha256"] for item in first["files"]}

    second = medya.apply_media(
        tmp_path, "klip.mp4", options={"outputs": ["thumbnail"]},
        capabilities=medya.disabled_capabilities())
    assert [item["status"] for item in second["outputs"]
            if item["kind"] == "thumbnail"] == ["replayed"]
    assert {item["path"]: item["sha256"] for item in second["files"]} == hashes
    assert len(list(tmp_path.rglob("*.png"))) == len(first["files"])


def test_apply_timeout_marks_the_half_written_output_failed(tmp_path):
    write_fake_mp4(tmp_path / "klip.mp4")
    caps = medya.disabled_capabilities()
    caps["ffmpeg"] = {"available": True, "path": "ffmpeg-does-not-exist",
                      "fallback": "stdlib-png"}
    caps["behaviors"]["preview"] = "ffmpeg"
    budget = medya.RenderBudget(0.5)

    result = medya.apply_media(
        tmp_path, "klip.mp4", options={"outputs": ["preview"],
                                       "preview_seconds": 15},
        capabilities=caps, budget=budget)

    assert result["ok"] is False
    assert result["failed"] and result["failed"][0]["kind"] == "preview"
    assert result["failed"][0]["error_class"] == "retryable"
    assert result["error"]["error_class"] == "retryable"
    assert not list((tmp_path / result["output_dir"]).glob("*.mp4")), \
        "yarım kalan çıktı silinmeli"
    assert (tmp_path / result["preview_frame"]).is_file()


def test_unapproved_delivery_is_refused_and_approved_one_is_evidenced(tmp_path):
    write_fake_mp4(tmp_path / "klip.mp4")
    caps = medya.disabled_capabilities()

    with pytest.raises(medya.MediaError) as error:
        medya.deliver(tmp_path, "klip.mp4", capabilities=caps)
    assert medya.error_payload(error.value)["error_class"] == "needs-user"

    gate = BridgeGate(registry())
    token = gate.issue_approval("medya", "apply")
    decision = gate.check(medya.APPROVER_PERSONA, "medya", "apply",
                          idempotency_key="medya-e2e-1",
                          approval_token=token)
    assert decision["approval_used"] is True
    assert decision["ledger_key"].startswith("medya:apply:")

    delivered = medya.deliver(
        tmp_path, "klip.mp4", options={"outputs": ["thumbnail", "strip"]},
        approved=True, capabilities=caps)
    assert delivered["ok"] is True
    evidence = delivered["evidence"]
    assert evidence["format"] == "medya-evidence"
    assert evidence["source_sha256"] == hashlib.sha256(
        (tmp_path / "klip.mp4").read_bytes()).hexdigest()
    assert evidence["files"]
    assert (tmp_path / evidence["preview_frame"]).is_file()
    for item in evidence["files"]:
        assert hashlib.sha256(
            (tmp_path / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert set(manifest()["evidence"]["fields"]) <= set(evidence)
    assert delivered["draft"]["scenes"]


# ------------------------------------------------------- optional real ffmpeg


@pytest.mark.skipif(
    not medya.discover_capabilities()["ffmpeg"]["available"]
    or not medya.discover_capabilities()["ffprobe"]["available"],
    reason="ffmpeg/ffprobe PATH'te yok")
def test_real_ffmpeg_produces_thumbnail_preview_and_strip(tmp_path):
    import shutil
    import subprocess

    source = tmp_path / "gercek.mp4"
    subprocess.run(
        [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
         "-pix_fmt", "yuv420p", str(source)],
        check=True, timeout=60, stdin=subprocess.DEVNULL)

    inventory_value = medya.read_media(tmp_path, "gercek.mp4")
    assert inventory_value["kind"] == "video"
    assert inventory_value["duration_seconds"] == pytest.approx(2.0, abs=0.2)
    assert inventory_value["capability_used"] == "ffprobe"

    result = medya.apply_media(
        tmp_path, "gercek.mp4",
        options={"outputs": ["thumbnail", "preview", "strip"],
                 "preview_seconds": 1, "timeout_seconds": 60},
        inventory_value=inventory_value)
    assert result["ok"] is True, result.get("failed")
    by_kind = {item["kind"]: item for item in result["outputs"]}
    assert by_kind["thumbnail"]["status"] == "ok"
    assert (tmp_path / by_kind["thumbnail"]["path"]).read_bytes()[:8] \
        == PNG_SIGNATURE
    preview = tmp_path / by_kind["preview"]["path"]
    assert preview.read_bytes()[4:8] == b"ftyp"
    assert preview.stat().st_size <= 2_000_000
    assert by_kind["strip"]["status"] == "ok"
