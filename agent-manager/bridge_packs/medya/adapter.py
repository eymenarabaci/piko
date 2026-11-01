"""Medya (video/görsel/ses) köprü paketi adaptörü.

Seviyeler
---------
``read``
    Bir medya dosyasının envanterini çıkarır: süre, çözünürlük ve codec
    ``ffprobe`` varsa ondan; yoksa dosya boyutu + uzantı + sihirli bayt
    tespitinden. WAV dosyaları için stdlib ``wave`` her zaman yedektir.
``draft``
    Diske hiçbir şey yazmadan kurgu planı üretir: sahne listesi, kırpma
    noktaları, altyazı taslağı ve thumbnail zamanları.
``apply``
    Onaylı ve bütçe sınırlı küçük çıktı üretir: thumbnail PNG, kısa preview
    (varsayılan tavan 15 sn / 2 MB) ve özet kare şeridi. Her ffmpeg çağrısı
    zorunlu bir zaman aşımıyla koşar; yarım kalan dosya silinir ve ``failed``
    işaretlenir.

Paket çökmemelidir: ffmpeg/ffprobe/Pillow yoksa davranış ``fallback`` olur.
Hata taksonomisi: eksik kaynak ``needs-user``, ffmpeg yok/çöktü
``external-down``, kodec/zaman aşımı/bütçe ``retryable``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import time
import wave
import zlib
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_ID = "medya"
EVIDENCE_FORMAT = "medya-evidence"
MAX_PREVIEW_SECONDS = 15.0
MAX_PREVIEW_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RENDER_SECONDS_PER_MINUTE = 120.0
BUDGET_PROFILE = {
    "render_seconds_per_minute": DEFAULT_RENDER_SECONDS_PER_MINUTE,
    "max_preview_seconds": MAX_PREVIEW_SECONDS,
    "max_preview_bytes": MAX_PREVIEW_BYTES,
}
OUTPUT_KINDS = ("thumbnail", "preview", "strip")
MEDIA_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
}


# --------------------------------------------------------------------------- errors


class MediaError(ValueError):
    """Base error that also names its taxonomy class."""

    error_class = "needs-user"


class NeedsUserMediaError(MediaError):
    error_class = "needs-user"


class RetryableMediaError(MediaError):
    error_class = "retryable"


class ExternalMediaError(MediaError):
    error_class = "external-down"


def error_payload(exc: Exception) -> dict:
    error_class = getattr(exc, "error_class", "needs-user")
    return {"bridge": BRIDGE_ID, "error_class": error_class,
            "message": str(exc)[:600]}


# ------------------------------------------------------------------ capabilities


def discover_capabilities() -> dict:
    """Probe optional providers and describe the fallback for each one."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    pillow = importlib.util.find_spec("PIL")
    all_ok = bool(ffmpeg and ffprobe)
    return {
        "bridge": BRIDGE_ID,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "ffmpeg": {
            "available": bool(ffmpeg),
            "path": ffmpeg or "",
            "fallback": "stdlib-png + preview-atlandı",
        },
        "ffprobe": {
            "available": bool(ffprobe),
            "path": ffprobe or "",
            "fallback": "sihirli-bayt + stdlib-wave",
        },
        "pillow": {
            "available": bool(pillow),
            "path": "PIL" if pillow else "",
            "fallback": "stdlib-png",
        },
        "behaviors": {
            "read": "ffprobe" if all_ok else ("ffprobe" if ffprobe else "magic-bytes/wave"),
            "thumbnail": "ffmpeg" if ffmpeg else ("pillow" if pillow else "stdlib-png"),
            "preview": "ffmpeg" if ffmpeg else "fallback",
            "strip": "ffmpeg" if ffmpeg else "stdlib-png",
        },
    }


def disabled_capabilities() -> dict:
    """A deterministic capability map for tests and offline runs."""
    return {
        "bridge": BRIDGE_ID,
        "probed_at": "disabled",
        "ffmpeg": {"available": False, "path": "", "fallback": "stdlib-png"},
        "ffprobe": {"available": False, "path": "", "fallback": "magic-bytes/wave"},
        "pillow": {"available": False, "path": "", "fallback": "stdlib-png"},
        "behaviors": {
            "read": "magic-bytes/wave",
            "thumbnail": "stdlib-png",
            "preview": "fallback",
            "strip": "stdlib-png",
        },
    }


def _caps(capabilities: dict | None) -> dict:
    return discover_capabilities() if capabilities is None else capabilities


def _has(capabilities: dict, name: str) -> bool:
    return bool((capabilities.get(name) or {}).get("available"))


def _cap_path(capabilities: dict, name: str) -> str:
    return str((capabilities.get(name) or {}).get("path") or "")


def write_capability_manifest(path) -> dict:
    """Persist the probe result next to the pack for the developer screen."""
    capabilities = discover_capabilities()
    Path(path).write_text(
        json.dumps(capabilities, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return capabilities


# ----------------------------------------------------------------------- helpers


def _root(workspace) -> Path:
    if not str(workspace or "").strip():
        raise NeedsUserMediaError("Çalışma alanı gerekli.")
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise NeedsUserMediaError("Çalışma alanı mevcut bir dizin olmalı.")
    return root


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve(root: Path, relative) -> Path:
    relative = str(relative or "").strip()
    if not relative:
        return root
    target = (root / relative).resolve()
    if not _inside(root, target):
        raise NeedsUserMediaError("Yol çalışma alanı dışında; reddedildi.")
    return target


def _relative(root: Path, target: Path) -> str:
    return str(target.resolve().relative_to(root.resolve())).replace("\\", "/")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_ASCII_FOLD = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def _slug(value: str) -> str:
    folded = str(value or "").translate(_ASCII_FOLD).casefold()
    cleaned = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return cleaned[:48] or "medya"


def _fmt_duration(seconds) -> str:
    value = _as_float(seconds)
    return "bilinmiyor" if value is None else f"{value:.2f} sn"


def _summary(inventory: dict, capability: str | None = None) -> str:
    used = capability or inventory.get("capability_used") or "magic-bytes"
    return "\n".join([
        f"süre: {_fmt_duration(inventory.get('duration_seconds'))}",
        f"boyut: {int(inventory.get('bytes') or 0)} bayt",
        f"capability: {used}",
    ])


# ---------------------------------------------------------------- magic / probe


def _detect_magic(head: bytes) -> tuple[str, str]:
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video", "mp4"
    if head[:4] == b"RIFF" and len(head) >= 12:
        if head[8:12] == b"WAVE":
            return "audio", "wav"
        if head[8:12] == b"AVI ":
            return "video", "avi"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "gif"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video", "webm"
    if head.startswith(b"OggS"):
        return "audio", "ogg"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "audio", "mp3"
    if head.startswith(b"fLaC"):
        return "audio", "flac"
    return "unknown", ""


def _suffix_kind(suffix: str) -> tuple[str, str]:
    suffix = str(suffix or "").lower()
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
        return "video", suffix.lstrip(".")
    if suffix in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}:
        return "audio", suffix.lstrip(".")
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image", suffix.lstrip(".")
    return "unknown", ""


def _probe_wave(target: Path) -> dict:
    with wave.open(str(target), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        return {
            "duration_seconds": frames / rate if rate else None,
            "sample_rate": rate,
            "channels": handle.getnchannels(),
            "sample_width": handle.getsampwidth(),
            "frames": frames,
        }


def _probe_ffprobe(target: Path, ffprobe: str, *, timeout: float = 10.0) -> dict:
    args = [ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(target)]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalMediaError(f"ffprobe çalıştırılamadı: {exc}") from exc
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace")[:240]
        raise ExternalMediaError(f"ffprobe okunamadı: {message}")
    try:
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except ValueError as exc:
        raise ExternalMediaError("ffprobe geçersiz JSON döndürdü.") from exc
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    info: dict = {
        "format_name": fmt.get("format_name"),
        "bit_rate": _as_int(fmt.get("bit_rate")),
        "duration_seconds": _as_float(fmt.get("duration")),
    }
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video:
        info.update({"width": _as_int(video.get("width")),
                     "height": _as_int(video.get("height")),
                     "codec": video.get("codec_name"),
                     "frames": _as_int(video.get("nb_frames")),
                     "kind": "video"})
        info["duration_seconds"] = info["duration_seconds"] or _as_float(video.get("duration"))
    elif audio:
        info.update({"sample_rate": _as_int(audio.get("sample_rate")),
                     "channels": _as_int(audio.get("channels")),
                     "codec": audio.get("codec_name"),
                     "kind": "audio"})
        info["duration_seconds"] = info["duration_seconds"] or _as_float(audio.get("duration"))
    return info


# --------------------------------------------------------------------------- read


def read_media(workspace, path, *, capabilities: dict | None = None) -> dict:
    """Return a media inventory. Never raises because an optional tool is absent."""
    root = _root(workspace)
    target = _resolve(root, path)
    if target.is_dir():
        return inventory(workspace, path, capabilities=capabilities)
    if not target.is_file():
        raise NeedsUserMediaError(f"Kaynak dosya bulunamadı: {path}")

    caps = _caps(capabilities)
    size = target.stat().st_size
    with target.open("rb") as handle:
        head = handle.read(64)
    kind, container = _detect_magic(head)
    suffix_kind, suffix_container = _suffix_kind(target.suffix)
    if kind == "unknown":
        kind = suffix_kind
        container = suffix_container

    notes: list[str] = []
    inventory_value = {
        "bridge": BRIDGE_ID,
        "action": "read",
        "path": _relative(root, target),
        "suffix": target.suffix.lower(),
        "bytes": size,
        "sha256": _sha256_file(target),
        "kind": kind if kind != "unknown" else "binary",
        "container": container,
        "duration_seconds": None,
        "width": None,
        "height": None,
        "codec": None,
        "sample_rate": None,
        "channels": None,
        "capability_used": "magic-bytes",
        "notes": notes,
    }

    if container == "wav":
        try:
            inventory_value.update(_probe_wave(target))
            inventory_value["capability_used"] = "wave"
        except (wave.Error, OSError) as exc:
            notes.append(f"wave okunamadı: {exc}")

    if _has(caps, "ffprobe"):
        try:
            probed = _probe_ffprobe(target, _cap_path(caps, "ffprobe"))
        except ExternalMediaError as exc:
            notes.append(f"ffprobe yedeğe düştü: {exc}")
        else:
            for key in ("duration_seconds", "width", "height", "codec",
                        "sample_rate", "channels", "frames", "bit_rate", "format_name"):
                if probed.get(key) is not None:
                    inventory_value[key] = probed[key]
            if probed.get("kind"):
                inventory_value["kind"] = probed["kind"]
            if probed.get("format_name") and not inventory_value["container"]:
                inventory_value["container"] = str(probed["format_name"]).split(",")[0]
            inventory_value["capability_used"] = "ffprobe"

    inventory_value["summary"] = _summary(inventory_value)
    return inventory_value


def inventory(workspace, path: str = "", *, capabilities: dict | None = None,
              limit: int = 50) -> dict:
    """Inventory a folder (or a single file) of media assets."""
    root = _root(workspace)
    target = _resolve(root, path)
    if target.is_file():
        return read_media(workspace, path, capabilities=capabilities)
    if not target.is_dir():
        raise NeedsUserMediaError(f"Dizin bulunamadı: {path or '.'}")
    limit = max(1, min(int(limit), 200))
    files = []
    for candidate in sorted(target.rglob("*")):
        if len(files) >= limit:
            break
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in MEDIA_SUFFIXES:
            try:
                with candidate.open("rb") as handle:
                    kind, _ = _detect_magic(handle.read(64))
            except OSError:
                continue
            if kind == "unknown":
                continue
        try:
            files.append(read_media(
                workspace, _relative(root, candidate), capabilities=capabilities))
        except MediaError:
            continue
    total_bytes = sum(int(item.get("bytes") or 0) for item in files)
    capability = "ffprobe" if _has(_caps(capabilities), "ffprobe") else "magic-bytes/wave"
    return {
        "bridge": BRIDGE_ID,
        "action": "read",
        "path": _relative(root, target) or ".",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "capability_used": capability,
        "summary": "\n".join([
            f"{len(files)} medya dosyası",
            f"{total_bytes} bayt",
            f"capability: {capability}",
        ]),
    }


# -------------------------------------------------------------------------- draft


def build_draft(inventory_value: dict, *, options: dict | None = None) -> dict:
    """Build a deterministic edit plan in memory. Nothing is written to disk."""
    if not isinstance(inventory_value, dict) or not inventory_value.get("sha256"):
        raise NeedsUserMediaError("Kurgu planı için önce bir medya envanteri okuyun.")
    options = dict(options or {})
    notes: list[str] = []
    duration = _as_float(inventory_value.get("duration_seconds"))
    if not duration:
        duration = _as_float(options.get("assumed_duration_seconds")) or 10.0
        notes.append("Süre bilinmiyor; plan varsayılan süre üzerinden yapıldı.")
    duration = max(duration, 0.5)

    requested = options.get("scene_count")
    try:
        scene_count = int(requested) if requested is not None else max(1, round(duration / 5))
    except (TypeError, ValueError):
        scene_count = max(1, round(duration / 5))
    scene_count = max(1, min(scene_count, 12))
    step = duration / scene_count

    scenes = []
    for index in range(scene_count):
        start = round(index * step, 3)
        end = round(duration if index == scene_count - 1 else (index + 1) * step, 3)
        scenes.append({"index": index, "start": start, "end": end,
                       "duration": round(max(0.0, end - start), 3)})
    trim_points = [{"start": scene["start"], "end": scene["end"]} for scene in scenes]

    provided = options.get("subtitles")
    if isinstance(provided, str):
        provided = [line for line in provided.splitlines() if line.strip()]
    if provided is not None and not isinstance(provided, (list, tuple)):
        raise NeedsUserMediaError("'subtitles' bir liste veya satırlı metin olmalı.")
    subtitles = []
    for scene in scenes:
        text = ""
        if provided and scene["index"] < len(provided):
            text = str(provided[scene["index"]])
        subtitles.append({"index": scene["index"], "start": scene["start"],
                          "end": scene["end"],
                          "text": (text.strip() or f"Sahne {scene['index'] + 1} altyazı taslağı")[:140]})

    times = options.get("thumbnail_times")
    if not times:
        sample_count = min(3, scene_count)
        times = [duration * (index + 0.5) / sample_count for index in range(sample_count)]
    thumbnail_times = []
    for value in times:
        number = _as_float(value)
        if number is None:
            continue
        thumbnail_times.append(round(max(0.0, min(number, max(0.0, duration - 0.05))), 3))
    thumbnail_times = sorted(set(thumbnail_times)) or [0.0]

    planned_outputs = [kind for kind in options.get("outputs") or OUTPUT_KINDS
                       if kind in OUTPUT_KINDS] or list(OUTPUT_KINDS)
    return {
        "bridge": BRIDGE_ID,
        "action": "draft",
        "source": {
            "path": inventory_value.get("path"),
            "sha256": inventory_value.get("sha256"),
            "kind": inventory_value.get("kind"),
            "duration_seconds": inventory_value.get("duration_seconds"),
        },
        "duration_seconds": round(duration, 3),
        "scenes": scenes,
        "trim_points": trim_points,
        "subtitles": subtitles,
        "thumbnail_times": thumbnail_times,
        "planned_outputs": planned_outputs,
        "notes": notes,
        "summary": "\n".join([
            f"plan: {scene_count} sahne",
            f"toplam: {duration:.2f} sn",
            f"altyazı: {len(subtitles)} satır",
        ]),
    }


# ------------------------------------------------------------------------- budget


class RenderBudget:
    """Per-minute render-time ceiling (manifest budget profile)."""

    def __init__(self, seconds_per_minute: float = DEFAULT_RENDER_SECONDS_PER_MINUTE,
                 *, clock=time.monotonic) -> None:
        self.seconds_per_minute = max(0.0, float(seconds_per_minute))
        self.clock = clock
        self._events: list[tuple[float, float]] = []
        self.reserved_seconds = 0.0

    def _prune(self, now: float) -> None:
        self._events = [(at, value) for at, value in self._events if now - at < 60.0]

    def reserve(self, seconds: float) -> float:
        seconds = max(0.0, float(seconds))
        now = self.clock()
        self._prune(now)
        used = sum(value for _, value in self._events)
        if used + seconds > self.seconds_per_minute + 1e-9:
            raise RetryableMediaError(
                f"Render bütçesi aşıldı: {used + seconds:.1f} sn > "
                f"{self.seconds_per_minute:.1f} sn/dk.")
        self._events.append((now, seconds))
        self.reserved_seconds += seconds
        return seconds

    def snapshot(self) -> dict:
        now = self.clock()
        self._prune(now)
        return {
            "seconds_per_minute": self.seconds_per_minute,
            "used_seconds": round(sum(value for _, value in self._events), 3),
            "reserved_seconds": round(self.reserved_seconds, 3),
        }


# ------------------------------------------------------------------------- render


def _run_ffmpeg(args, *, timeout: float, output_path: Path | None = None) -> float:
    """Run one ffmpeg command with a mandatory timeout; kill on overrun."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise ExternalMediaError(f"ffmpeg başlatılamadı: {exc}") from exc
    try:
        _, stderr = proc.communicate(timeout=max(0.1, float(timeout)))
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        if output_path is not None:
            _safe_unlink(output_path)
        raise RetryableMediaError(
            f"Render zaman aşımı ({timeout:.1f} sn); yarım çıktı silindi.") from exc
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        if output_path is not None:
            _safe_unlink(output_path)
        message = (stderr or b"").decode("utf-8", errors="replace")[-300:]
        raise ExternalMediaError(f"ffmpeg çöktü (kod {proc.returncode}): {message}")
    if output_path is not None:
        path = Path(output_path)
        if not path.is_file() or path.stat().st_size <= 0:
            _safe_unlink(path)
            raise ExternalMediaError("ffmpeg çıktı üretmedi.")
    return elapsed


def _safe_unlink(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _stdlib_png(width: int = 320, height: int = 180, *, seed: int = 0,
                bars: int = 0) -> bytes:
    """A valid deterministic PNG without Pillow or ffmpeg."""
    width = max(1, int(width))
    height = max(1, int(height))
    bars = max(0, min(int(bars), width))
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            if bars and (x * bars // width) % 2 == 0:
                shade = (x * 255 // width)
                row.extend(((shade + seed) & 255, (shade * 2 + 40) & 255, 90))
            else:
                red = (x * 255 // max(1, width - 1) + seed) & 255
                green = (y * 255 // max(1, height - 1)) & 255
                blue = (seed * 7 + x + y) & 255
                row.extend((red, green, blue))
        rows.append(bytes(row))
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(raw, 9))
            + _png_chunk(b"IEND", b""))


def _fallback_png(destination: Path, *, kind: str, seed: int) -> None:
    if kind == "strip":
        data = _stdlib_png(640, 160, seed=seed, bars=8)
    else:
        data = _stdlib_png(320, 180, seed=seed)
    destination.write_bytes(data)


def _output_key(source_sha256: str, kind: str, options: dict) -> str:
    material = json.dumps({"source": source_sha256, "kind": kind, "options": options},
                          ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _entry(kind: str, destination: Path, root: Path, key: str, *,
           status: str, capability: str, note: str = "") -> dict:
    data = destination.read_bytes() if destination.is_file() else b""
    return {
        "kind": kind,
        "path": _relative(root, destination) if destination.is_file() else None,
        "sha256": hashlib.sha256(data).hexdigest() if data else None,
        "bytes": len(data),
        "status": status,
        "capability": capability,
        "idempotency_key": key,
        "note": note,
    }


def _fallback_entry(kind: str, key: str, reason: str) -> dict:
    return {"kind": kind, "path": None, "sha256": None, "bytes": 0,
            "status": "fallback", "capability": "none",
            "idempotency_key": key, "note": reason}


def _failed_entry(kind: str, key: str, reason: str,
                  error_class: str = "external-down") -> dict:
    return {"kind": kind, "path": None, "sha256": None, "bytes": 0,
            "status": "failed", "capability": "none", "error_class": error_class,
            "idempotency_key": key, "note": reason}


def _thumbnail(ffmpeg: str, source: Path, destination: Path, *, kind: str,
               at: float, timeout: float, budget: RenderBudget) -> None:
    budget.reserve(1.0)
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{max(0.0, at):.3f}", "-i", str(source),
            "-frames:v", "1", "-an", "-vf", "scale=320:-2", str(destination)]
    _run_ffmpeg(args, timeout=timeout, output_path=destination)


def _strip(ffmpeg: str, source: Path, destination: Path, *,
           timeout: float, budget: RenderBudget) -> None:
    budget.reserve(2.0)
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-vf", "fps=1/2,scale=160:-2,tile=4x1:padding=4:margin=4",
            "-frames:v", "1", str(destination)]
    _run_ffmpeg(args, timeout=timeout, output_path=destination)


def _preview(ffmpeg: str, source: Path, destination: Path, *, kind: str,
             start: float, seconds: float, max_bytes: int,
             timeout: float, budget: RenderBudget) -> None:
    budget.reserve(seconds)
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}",
            "-i", str(source)]
    if kind == "audio":
        args += ["-vn", "-c:a", "aac", "-b:a", "96k"]
    else:
        args += ["-an", "-r", "12", "-vf", "scale=320:-2", "-pix_fmt", "yuv420p"]
    args += ["-fs", str(int(max_bytes)), str(destination)]
    _run_ffmpeg(args, timeout=timeout, output_path=destination)
    size = destination.stat().st_size
    if size > max_bytes:
        _safe_unlink(destination)
        raise ExternalMediaError(
            f"Preview boyut bütçesi aşıldı: {size} > {max_bytes} bayt.")


# -------------------------------------------------------------------------- apply


def apply_media(workspace, source, *, output_dir: str = "medya-cikti",
                options: dict | None = None, inventory_value: dict | None = None,
                capabilities: dict | None = None,
                budget: RenderBudget | None = None) -> dict:
    """Produce small approved outputs. Missing tools degrade to fallback."""
    options = dict(options or {})
    caps = _caps(capabilities)
    root = _root(workspace)
    src = _resolve(root, source)
    if not src.is_file():
        raise NeedsUserMediaError(f"Kaynak dosya bulunamadı: {source}")
    inv = inventory_value or read_media(workspace, source, capabilities=caps)
    source_sha = str(inv.get("sha256") or "")

    folder = _resolve(root, output_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExternalMediaError(f"Çıktı klasörü oluşturulamadı: {exc}") from exc

    profile = dict(BUDGET_PROFILE)
    profile.update(options.get("budget") or {})
    budget = budget or RenderBudget(profile["render_seconds_per_minute"])

    requested = options.get("outputs") or list(OUTPUT_KINDS)
    if isinstance(requested, str):
        requested = [requested]
    requested = [kind for kind in requested if kind in OUTPUT_KINDS]
    if not requested:
        raise NeedsUserMediaError("Geçerli çıktı türü istenmedi.")

    max_preview = min(
        float(options.get("preview_seconds") or profile["max_preview_seconds"]),
        MAX_PREVIEW_SECONDS, float(profile["max_preview_seconds"]))
    max_preview = max(0.5, max_preview)
    max_bytes = int(min(
        int(options.get("preview_max_bytes") or profile["max_preview_bytes"]),
        MAX_PREVIEW_BYTES, int(profile["max_preview_bytes"])))
    timeout = max(1.0, min(float(options.get("timeout_seconds") or
                                 DEFAULT_TIMEOUT_SECONDS), 120.0))
    start = max(0.0, float(options.get("preview_start") or 0.0))
    duration = _as_float(inv.get("duration_seconds"))
    if duration and start >= duration:
        start = 0.0
    kind = str(inv.get("kind") or "binary")
    ffmpeg = _cap_path(caps, "ffmpeg") if _has(caps, "ffmpeg") else ""
    seed = int(source_sha[:8] or "0", 16)
    stem = _slug(src.stem)

    entries: list[dict] = []
    for output_kind in requested:
        key_options = {"preview_seconds": max_preview if output_kind == "preview" else None,
                       "preview_start": start if output_kind == "preview" else None,
                       "preview_max_bytes": max_bytes if output_kind == "preview" else None,
                       "at": (options.get("thumbnail_times") or [0.0])[0]
                       if output_kind == "thumbnail" else None}
        key = _output_key(source_sha, output_kind, key_options)
        if output_kind == "preview":
            suffix = ".m4a" if kind == "audio" else ".mp4"
        else:
            suffix = ".png"
        destination = folder / f"{stem}-{output_kind}-{key[:12]}{suffix}"
        if destination.is_file() and destination.stat().st_size > 0:
            entries.append(_entry(output_kind, destination, root, key,
                                  status="replayed", capability="cached"))
            continue
        try:
            if output_kind == "thumbnail":
                if ffmpeg and kind in {"video", "image"}:
                    try:
                        _thumbnail(ffmpeg, src, destination, kind=kind,
                                   at=float(key_options["at"] or 0.0),
                                   timeout=timeout, budget=budget)
                        entries.append(_entry("thumbnail", destination, root, key,
                                              status="ok", capability="ffmpeg"))
                        continue
                    except RetryableMediaError:
                        raise
                    except ExternalMediaError as exc:
                        _safe_unlink(destination)
                        _fallback_png(destination, kind="thumbnail", seed=seed)
                        entries.append(_entry("thumbnail", destination, root, key,
                                              status="fallback", capability="stdlib-png",
                                              note=str(exc)))
                        continue
                _fallback_png(destination, kind="thumbnail", seed=seed)
                entries.append(_entry("thumbnail", destination, root, key,
                                      status="fallback", capability="stdlib-png",
                                      note="ffmpeg yok; stdlib PNG kullanıldı."))
                continue

            if output_kind == "strip":
                if ffmpeg and kind == "video":
                    try:
                        _strip(ffmpeg, src, destination, timeout=timeout, budget=budget)
                        entries.append(_entry("strip", destination, root, key,
                                              status="ok", capability="ffmpeg"))
                        continue
                    except RetryableMediaError:
                        raise
                    except ExternalMediaError as exc:
                        _safe_unlink(destination)
                        _fallback_png(destination, kind="strip", seed=seed)
                        entries.append(_entry("strip", destination, root, key,
                                              status="fallback", capability="stdlib-png",
                                              note=str(exc)))
                        continue
                _fallback_png(destination, kind="strip", seed=seed)
                entries.append(_entry("strip", destination, root, key,
                                      status="fallback", capability="stdlib-png",
                                      note="ffmpeg yok; özet şerit stdlib PNG."))
                continue

            if output_kind == "preview":
                if not ffmpeg:
                    entries.append(_fallback_entry(
                        "preview", key, "ffmpeg yok; preview atlandı (fallback)."))
                    continue
                if kind == "image":
                    entries.append(_fallback_entry(
                        "preview", key, "Görsel için preview gerekmez (fallback)."))
                    continue
                try:
                    _preview(ffmpeg, src, destination, kind=kind, start=start,
                             seconds=max_preview, max_bytes=max_bytes,
                             timeout=timeout, budget=budget)
                except RetryableMediaError as exc:
                    _safe_unlink(destination)
                    entries.append(_failed_entry("preview", key, str(exc),
                                                 "retryable"))
                    continue
                except ExternalMediaError as exc:
                    _safe_unlink(destination)
                    entries.append(_failed_entry("preview", key, str(exc),
                                                 "external-down"))
                    continue
                entries.append(_entry("preview", destination, root, key,
                                      status="ok", capability="ffmpeg"))
                continue
        except RetryableMediaError as exc:
            _safe_unlink(destination)
            entries.append(_failed_entry(output_kind, key, str(exc)))

    png_entries = [item for item in entries
                   if item.get("path") and str(item["path"]).endswith(".png")]
    if png_entries:
        preview_frame = png_entries[0]["path"]
    else:
        frame_key = _output_key(source_sha, "preview_frame", {})
        frame = folder / f"{stem}-preview-frame-{frame_key[:12]}.png"
        if not frame.is_file():
            _fallback_png(frame, kind="thumbnail", seed=seed)
        frame_entry = _entry("preview_frame", frame, root, frame_key,
                             status="fallback", capability="stdlib-png",
                             note="Önizleme karesi stdlib PNG ile üretildi.")
        entries.append(frame_entry)
        preview_frame = frame_entry["path"]

    files = [item for item in entries if item.get("sha256")]
    failed = [item for item in entries if item["status"] == "failed"]
    capability_used = sorted({item["capability"] for item in files
                              if item.get("capability")})
    ok = bool(files)
    result = {
        "bridge": BRIDGE_ID,
        "action": "apply",
        "ok": ok,
        "source": {"path": inv.get("path"), "sha256": source_sha,
                   "bytes": inv.get("bytes"), "kind": kind,
                   "duration_seconds": inv.get("duration_seconds")},
        "source_sha256": source_sha,
        "output_dir": _relative(root, folder),
        "outputs": entries,
        "files": files,
        "failed": failed,
        "preview_frame": preview_frame,
        "preview_seconds": max_preview,
        "preview_max_bytes": max_bytes,
        "timeout_seconds": timeout,
        "capability_used": capability_used,
        "budget": budget.snapshot(),
        "summary": _summary(inv, ", ".join(capability_used) or "fallback"),
        "generated_at": _now_iso(),
    }
    if not ok:
        reason = failed[0]["note"] if failed else "Hiçbir çıktı üretilemedi."
        result["error"] = error_payload(ExternalMediaError(reason))
    return result


def build_evidence(inventory_value: dict, applied: dict) -> dict:
    """The single evidence shape the delivery record and developer screen read."""
    files = [
        {"kind": item.get("kind"), "path": item.get("path"),
         "sha256": item.get("sha256"), "bytes": item.get("bytes"),
         "status": item.get("status")}
        for item in applied.get("files") or []
    ]
    source_sha = str(applied.get("source_sha256")
                     or inventory_value.get("sha256") or "")
    capability = ", ".join(applied.get("capability_used") or []) or "fallback"
    summary = "\n".join([
        f"süre: {_fmt_duration(inventory_value.get('duration_seconds'))}",
        f"boyut: {int(inventory_value.get('bytes') or 0)} bayt kaynak, "
        f"{sum(int(item['bytes']) for item in files)} bayt çıktı",
        f"capability: {capability}",
    ])
    return {
        "format": EVIDENCE_FORMAT,
        "bridge": BRIDGE_ID,
        "ok": bool(applied.get("ok")) and bool(files),
        "source": {
            "path": inventory_value.get("path"),
            "sha256": source_sha,
            "bytes": inventory_value.get("bytes"),
            "duration_seconds": inventory_value.get("duration_seconds"),
        },
        "source_sha256": source_sha,
        "files": files,
        "sha256": {item["path"]: item["sha256"] for item in files},
        "preview_frame": applied.get("preview_frame"),
        "failed_outputs": list(applied.get("failed") or []),
        "capability_used": list(applied.get("capability_used") or []),
        "summary": summary,
        "generated_at": _now_iso(),
    }


def deliver(workspace, source, *, output_dir: str = "medya-cikti",
            options: dict | None = None, approved: bool = False,
            capabilities: dict | None = None,
            budget: RenderBudget | None = None) -> dict:
    """read -> draft -> apply -> evidence, all inside the workspace."""
    if not approved:
        raise NeedsUserMediaError(
            "apply için kullanıcı onayı gerekli (approved=True).")
    caps = _caps(capabilities)
    inventory_value = read_media(workspace, source, capabilities=caps)
    draft = build_draft(inventory_value, options=options)
    applied = apply_media(workspace, source, output_dir=output_dir,
                          options=options, inventory_value=inventory_value,
                          capabilities=caps, budget=budget)
    evidence = build_evidence(inventory_value, applied)
    return {"ok": evidence["ok"], "inventory": inventory_value,
            "draft": draft, "applied": applied, "evidence": evidence}


# ------------------------------------------------------------------------ adapter


class MedyaAdapter:
    """Adapter loaded lazily by the bridge registry."""

    bridge_id = BRIDGE_ID
    levels = ("read", "draft", "apply")

    def capabilities(self) -> dict:
        return discover_capabilities()

    def read(self, workspace, *, path: str = "", capabilities: dict | None = None) -> dict:
        return inventory(workspace, path, capabilities=capabilities)

    def draft(self, inventory_value: dict, *, options: dict | None = None) -> dict:
        return build_draft(inventory_value, options=options)

    def apply(self, workspace, source, *, output_dir: str = "medya-cikti",
              options: dict | None = None,
              inventory_value: dict | None = None,
              capabilities: dict | None = None,
              budget: RenderBudget | None = None) -> dict:
        return apply_media(workspace, source, output_dir=output_dir,
                           options=options, inventory_value=inventory_value,
                           capabilities=capabilities, budget=budget)


def adapter() -> MedyaAdapter:
    return MedyaAdapter()


def manifest() -> dict:
    path = Path(__file__).with_name("manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def register(registry) -> dict:
    """Register (or refresh) the medya pack in a BridgeRegistry."""
    return registry.register(manifest(), replace=True)


MANAGER_PERSONA = {"role": "scheduler",
                   "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}
APPROVER_PERSONA = {"role": "implementer",
                    "bridges": {BRIDGE_ID: {"levels": ["read", "draft", "apply"]}}}
