"""Bounded local vector image and motion-graphics rendering for CAO workers.

This renderer is deterministic: an agent supplies a small JSON scene, never
Python, SVG markup, FFmpeg options, or an output path. It does not implement a
diffusion image model or text-to-video model.
"""
from __future__ import annotations

import asyncio
import base64
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .processes import run_command


HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
VIDEO_WIDTH = 320
VIDEO_HEIGHT = 180
MAX_VIDEO_FRAMES = 36


class MediaError(ValueError):
    pass


class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rectangle", "circle", "text"]
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(default=0.2, gt=0, le=1)
    height: float = Field(default=0.2, gt=0, le=1)
    radius: float = Field(default=0.1, gt=0, le=1)
    color: str = Field(default="#ffffff", pattern=HEX_COLOR)
    text: str = Field(default="", max_length=120)
    font_size: int = Field(default=32, ge=8, le=72)
    dx: float = Field(default=0, ge=-1, le=1)
    dy: float = Field(default=0, ge=-1, le=1)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Metin kontrol karakteri içeremez.")
        return value

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind == "text" and not self.text.strip():
            raise ValueError("Metin şeklinin text alanı boş olamaz.")
        if self.kind != "text" and self.text:
            raise ValueError("Metin olmayan şekilde text alanı kullanılamaz.")
        return self


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(default=640, ge=320, le=1024)
    height: int = Field(default=360, ge=180, le=1024)
    background: str = Field(default="#101827", pattern=HEX_COLOR)
    shapes: list[Shape] = Field(min_length=1, max_length=20)
    duration_seconds: float = Field(default=2, ge=1, le=4)
    fps: int = Field(default=8, ge=4, le=12)


def media_scene_instructions(role: Literal["image_generator", "video_generator"], task: str) -> str:
    """Ask a local agent for a strictly bounded JSON drawing specification."""
    if role not in {"image_generator", "video_generator"} or not isinstance(task, str) or not task.strip():
        raise MediaError("Geçersiz medya işçisi isteği.")
    if len(task) > 6000:
        raise MediaError("Medya görevi 6000 karakteri aşamaz.")
    kind = "SVG vektör görsel" if role == "image_generator" else "kısa soyut MP4 hareketli grafik"
    restriction = ("Metin şekli kullanılabilir." if role == "image_generator" else
                   "Video için yalnız rectangle ve circle şekilleri kullan; ses ve metin yok.")
    return (
        f"İstenen işi {kind} olarak çizilecek bir sahneye dönüştür. Yalnız tek JSON nesnesi döndür; "
        "markdown veya açıklama ekleme. Alanlar: width, height, background (#RRGGBB), "
        "shapes (1-20 nesne), duration_seconds (1-4), fps (4-12). "
        "Her shape: kind=rectangle|circle|text, x/y=0..1, width/height/radius=0..1, "
        "color=#RRGGBB, text=en çok 120 karakter, font_size=8..72, dx/dy=-1..1. "
        f"{restriction} Görsel dosyası üretildiğini iddia etme; dosyayı yerel çizici oluşturacak.\n"
        f"İstek: {task.strip()}"
    )


def parse_scene(text: str, *, video: bool = False) -> Scene:
    if not isinstance(text, str) or len(text) > 20000:
        raise MediaError("Medya sahnesi metin biçiminde ve 20 KB altında olmalı.")
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        scene = Scene.model_validate(json.loads(value))
    except (ValueError, TypeError) as exc:
        raise MediaError("Geçersiz veya sınırları aşan medya sahnesi.") from exc
    if video:
        if any(shape.kind == "text" for shape in scene.shapes):
            raise MediaError("Yerel video çizicisi metin şekli desteklemiyor.")
        if len(scene.shapes) > 10 or round(scene.duration_seconds * scene.fps) > MAX_VIDEO_FRAMES:
            raise MediaError("Video için en çok 10 şekil ve 36 kare kullanılabilir.")
    return scene


def _output_dir(workspace: str | Path) -> Path:
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise MediaError("Çalışma alanı klasör olmalı.")
    folder = root / "agent-manager-output"
    folder.mkdir(exist_ok=True)
    if not folder.resolve(strict=True).is_relative_to(root):
        raise MediaError("Çıktı klasörü çalışma alanı dışında kalıyor.")
    return folder


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(bytes.fromhex(color[1:]))  # type: ignore[return-value]


def render_svg(scene: Scene, workspace: str | Path) -> dict:
    """Create an escaped, self-contained SVG. No scripts, external URLs or raw SVG."""
    folder = _output_dir(workspace)
    path = folder / f"image-{uuid4().hex}.svg"
    fragments = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{scene.width}" height="{scene.height}" '
        f'viewBox="0 0 {scene.width} {scene.height}">',
        f'<rect width="100%" height="100%" fill="{scene.background}"/>',
    ]
    for shape in scene.shapes:
        x, y = round(shape.x * scene.width, 2), round(shape.y * scene.height, 2)
        if shape.kind == "rectangle":
            fragments.append(f'<rect x="{x}" y="{y}" width="{round(shape.width * scene.width, 2)}" '
                             f'height="{round(shape.height * scene.height, 2)}" fill="{shape.color}"/>')
        elif shape.kind == "circle":
            fragments.append(f'<circle cx="{x}" cy="{y}" r="{round(shape.radius * min(scene.width, scene.height), 2)}" '
                             f'fill="{shape.color}"/>')
        else:
            fragments.append(f'<text x="{x}" y="{y}" fill="{shape.color}" font-size="{shape.font_size}" '
                             f'font-family="Arial, sans-serif">{escape(shape.text)}</text>')
    fragments.append("</svg>")
    path.write_text("\n".join(fragments), encoding="utf-8", newline="\n")
    return {"kind": "image", "format": "svg", "mime_type": "image/svg+xml",
            "path": str(path), "width": scene.width, "height": scene.height,
            "bytes": path.stat().st_size, "generator": "local_vector_renderer"}


def _frame(scene: Scene, progress: float) -> bytes:
    width, height = VIDEO_WIDTH, VIDEO_HEIGHT
    background = bytes(_rgb(scene.background))
    frame = bytearray(background * (width * height))
    for shape in scene.shapes:
        x = round((shape.x + shape.dx * progress) * width)
        y = round((shape.y + shape.dy * progress) * height)
        color = bytes(_rgb(shape.color))
        if shape.kind == "rectangle":
            left, right = max(0, x), min(width, x + round(shape.width * width))
            top, bottom = max(0, y), min(height, y + round(shape.height * height))
            row = color * max(0, right - left)
            for yy in range(top, bottom):
                offset = (yy * width + left) * 3
                frame[offset:offset + len(row)] = row
        elif shape.kind == "circle":
            radius = round(shape.radius * min(width, height))
            for yy in range(max(0, y - radius), min(height, y + radius + 1)):
                distance = radius * radius - (yy - y) ** 2
                half = int(distance ** 0.5) if distance >= 0 else 0
                left, right = max(0, x - half), min(width, x + half + 1)
                row = color * max(0, right - left)
                offset = (yy * width + left) * 3
                frame[offset:offset + len(row)] = row
    return bytes(frame)


async def render_mp4(scene: Scene, workspace: str | Path, *, ffmpeg: str | None = None,
                     on_event=None) -> dict:
    """Render at most 36 small RGB frames, then encode with tracked FFmpeg."""
    if any(shape.kind == "text" for shape in scene.shapes):
        raise MediaError("Video metin şekli desteklemiyor.")
    frames = round(scene.duration_seconds * scene.fps)
    if len(scene.shapes) > 10 or frames > MAX_VIDEO_FRAMES:
        raise MediaError("Video kaynak sınırı aşıldı.")
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise MediaError("MP4 üretimi için FFmpeg bulunamadı.")
    folder = _output_dir(workspace)
    token = uuid4().hex
    raw = folder / f"video-{token}.rgb"
    output = folder / f"video-{token}.mp4"
    completed = False
    async def silent_event(*_args):
        return None
    try:
        with raw.open("wb") as stream:
            for index in range(frames):
                stream.write(_frame(scene, index / max(1, frames - 1)))
        command = [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
                   "-framerate", str(scene.fps), "-i", str(raw), "-frames:v", str(frames),
                   "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(output)]
        result = await run_command(command, root=Path(__file__).resolve().parents[1],
                                   workspace=folder, timeout=45, on_event=on_event or silent_event)
        if result["returncode"] != 0 or not output.is_file() or output.stat().st_size < 1024:
            raise MediaError("FFmpeg MP4 dosyasını oluşturamadı.")
        with output.open("rb") as stream:
            stream.seek(4)
            if stream.read(4) != b"ftyp":
                raise MediaError("MP4 çıktı imzası doğrulanamadı.")
        completed = True
        return {"kind": "video", "format": "mp4", "mime_type": "video/mp4",
                "path": str(output), "width": VIDEO_WIDTH, "height": VIDEO_HEIGHT,
                "duration_seconds": frames / scene.fps, "bytes": output.stat().st_size,
                "generator": "local_motion_graphics_renderer"}
    except (OSError, TimeoutError) as exc:
        raise MediaError("Yerel video kodlayıcı çalıştırılamadı.") from exc
    finally:
        raw.unlink(missing_ok=True)
        if not completed:
            output.unlink(missing_ok=True)


async def generate_openai_image(prompt: str, workspace: str | Path, api_key: str) -> dict:
    folder = _output_dir(workspace)
    path = folder / f"image-openai-{uuid4().hex}.png"
    clean_prompt = prompt.strip()[:1000]
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "dall-e-3",
                "prompt": clean_prompt,
                "n": 1,
                "size": "1024x1024"
            }
        )
        resp.raise_for_status()
        data = resp.json()
        image_url = data["data"][0]["url"]
        img_resp = await client.get(image_url)
        img_resp.raise_for_status()
        path.write_bytes(img_resp.content)
    return {
        "kind": "image", "format": "png", "mime_type": "image/png",
        "path": str(path), "width": 1024, "height": 1024,
        "bytes": path.stat().st_size, "generator": "openai_dalle3"
    }


async def generate_google_image(prompt: str, workspace: str | Path, api_key: str) -> dict:
    folder = _output_dir(workspace)
    path = folder / f"image-google-{uuid4().hex}.png"
    clean_prompt = prompt.strip()[:1000]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={api_key}"
    payload = {
        "instances": [{"prompt": clean_prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "1:1"}
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        b64_bytes = data["predictions"][0]["bytesBase64Encoded"]
        path.write_bytes(base64.b64decode(b64_bytes))
    return {
        "kind": "image", "format": "png", "mime_type": "image/png",
        "path": str(path), "width": 1024, "height": 1024,
        "bytes": path.stat().st_size, "generator": "google_imagen3"
    }


async def create_media(role: Literal["image_generator", "video_generator"],
                       scene_json: str, workspace: str | Path, *, ffmpeg: str | None = None,
                       on_event=None) -> dict:
    if role not in {"image_generator", "video_generator"}:
        raise MediaError("Bilinmeyen medya işçisi.")
    
    # Check if cloud generation is configured via environment
    openai_key = os.getenv("OPENAI_API_KEY")
    google_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if role == "image_generator":
        if openai_key:
            try:
                return await generate_openai_image(scene_json, workspace, openai_key)
            except Exception as e:
                if on_event:
                    await on_event("media_fallback", f"OpenAI DALL-E çağrısı başarısız: {e}. Yerel SVG'ye geçiliyor.")
        elif google_key:
            try:
                return await generate_google_image(scene_json, workspace, google_key)
            except Exception as e:
                if on_event:
                    await on_event("media_fallback", f"Google Imagen çağrısı başarısız: {e}. Yerel SVG'ye geçiliyor.")

    scene = parse_scene(scene_json, video=role == "video_generator")
    if role == "image_generator":
        return render_svg(scene, workspace)
    return await render_mp4(scene, workspace, ffmpeg=ffmpeg, on_event=on_event)

