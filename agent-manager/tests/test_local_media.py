"""Safety and real-file checks for the bounded local renderer."""
import json
from pathlib import Path
import shutil

import pytest

from manager.local_media import (MediaError, create_media, media_scene_instructions,
                                 parse_scene, render_svg)


def scene(**overrides):
    value = {"background": "#101827", "shapes": [
        {"kind": "rectangle", "x": 0.1, "y": 0.2,
         "width": 0.3, "height": 0.2, "color": "#ff8800", "dx": 0.2},
    ]}
    return json.dumps(value | overrides)


def test_svg_is_real_file_and_escapes_text(tmp_path):
    item = parse_scene(scene(shapes=[{"kind": "text", "x": .1, "y": .5,
                                      "text": "<script>alert(1)</script>",
                                      "color": "#ffffff"}]))
    output = render_svg(item, tmp_path)
    path = Path(output["path"])
    assert path.is_relative_to(tmp_path)
    assert path.is_file() and output["bytes"] > 100
    content = path.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in content and "<script>" not in content
    assert "http://" not in content.replace('http://www.w3.org/2000/svg', '')


def test_scene_rejects_unbounded_or_executable_content():
    with pytest.raises(MediaError):
        parse_scene(scene(shapes=[{"kind": "rectangle", "x": 0, "y": 0,
                                    "color": "url(https://evil)"}]))
    with pytest.raises(MediaError):
        parse_scene(scene(shapes=[{"kind": "rectangle", "x": 0, "y": 0,
                                    "color": "#ffffff", "script": "alert(1)"}]))
    with pytest.raises(MediaError):
        parse_scene(scene(shapes=[{"kind": "text", "x": .1, "y": .2,
                                    "text": "title"}]), video=True)
    with pytest.raises(MediaError):
        parse_scene(scene(shapes=[{"kind": "circle", "x": .2, "y": .3}] * 21))


def test_media_prompt_describes_honest_generator():
    text = media_scene_instructions("video_generator", "Kısa hareketli grafik")
    assert "MP4" in text and "dosyayı yerel çizici oluşturacak" in text
    with pytest.raises(MediaError):
        media_scene_instructions("image_generator", "")


@pytest.mark.asyncio
async def test_real_short_mp4_and_rgb_cleanup(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg kurulu değil")
    output = await create_media("video_generator", scene(duration_seconds=1, fps=4), tmp_path)
    path = Path(output["path"])
    assert path.is_file() and path.stat().st_size > 1024
    assert path.read_bytes()[4:8] == b"ftyp"
    assert output["duration_seconds"] == 1
    assert not list(path.parent.glob("*.rgb"))
